import ast
import errno
from hashlib import sha256
import importlib
import importlib.machinery
import importlib.metadata
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import sys
from tempfile import TemporaryDirectory
from types import ModuleType

from packaging.requirements import Requirement

from . import db
from .config import ROOT

FINANCIAL_DATASETS = {"profit", "operation", "growth", "balance", "cash_flow", "dupont", "express", "forecast"}
SUPPORTED_DATASETS = FINANCIAL_DATASETS | {"bars:d", "bars:w", "bars:m", "bars:5", "bars:15", "bars:30", "bars:60", "factors", "dividends", "universe"}


def validate_manifest(manifest):
    if manifest.get("api_version") != "1" or manifest.get("frequency") not in {"d", "w", "m", "5", "15", "30", "60"}:
        raise ValueError("当前执行接口为版本 1，支持日/周/月及 5/15/30/60 分钟信号")
    if type(manifest.get("lookback")) is not int or not 1 <= manifest["lookback"] <= 10000:
        raise ValueError("预热长度 lookback 必须为 1–10000 的整数")
    datasets = manifest.get("datasets", [])
    if not isinstance(datasets, list) or not all(isinstance(d, str) and d in SUPPORTED_DATASETS for d in datasets):
        raise ValueError("必要数据只支持官方行情周期、公司行动、声明式历史证券池和八类有公告日期的财务数据；宏观五类当前仅供浏览/导出，不接受历史策略声明")
    if "bars:" + manifest["frequency"] not in datasets:
        raise ValueError("datasets 必须声明与 frequency 一致的行情数据，例如日线 bars:d")
    if any(d.startswith("bars:") and d != "bars:"+manifest["frequency"] for d in datasets):
        raise ValueError("行情窗口须与清单 frequency 一致；其它周期不会静默进入同一 history")
    fields = manifest.get("required_fields", {})
    if not isinstance(fields, dict) or set(fields) - (set(datasets) & FINANCIAL_DATASETS):
        raise ValueError("required_fields 只能为已声明的财务数据指定必要字段")
    if any(not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names) for names in fields.values()):
        raise ValueError("必要字段必须为非空字段名称数组")
    definitions = manifest.get("parameters")
    if not isinstance(definitions, dict):
        raise ValueError("parameters 必须为参数定义对象")
    for key, spec in definitions.items():
        if not isinstance(spec, dict) or spec.get("type") not in {"integer", "number", "boolean", "string"}:
            raise ValueError(f"{key} 的参数类型不受支持")
        for bound in ("minimum", "maximum"):
            if bound in spec and (spec["type"] not in {"integer", "number"} or type(spec[bound]) not in {int, float} or not math.isfinite(spec[bound])):
                raise ValueError(f"{key} 的范围必须为有限数值")
        if "minimum" in spec and "maximum" in spec and spec["minimum"] > spec["maximum"]:
            raise ValueError(f"{key} 的最小值大于最大值")
        if "enum" in spec and (not isinstance(spec["enum"], list) or not spec["enum"]):
            raise ValueError(f"{key} 的 enum 必须为非空数组")
    validate_parameters(manifest, {})


def digest_directory(directory):
    digest = sha256()
    for file in sorted(directory.rglob("*")):
        if file.is_symlink():
            raise ValueError("策略包不接受符号链接")
        if file.is_file() and "__pycache__" not in file.parts and file.suffix.lower() != ".pyc":
            digest.update(file.relative_to(directory).as_posix().encode())
            digest.update(file.read_bytes())
    return digest.hexdigest()


def archive_directory(source, target, digest):
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with TemporaryDirectory(prefix=".archive-", dir=target.parent) as temporary:
            staged = Path(temporary) / "content"
            shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            if digest_directory(staged) != digest:
                raise ValueError("归档复制期间源文件发生变化，请检查源目录后重试")
            try:
                staged.rename(target)
            except OSError as exc:
                if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
    if digest_directory(target) != digest:
        raise ValueError("归档文件摘要变化，请恢复原版本")


def read_source_file(file):
    try:
        return file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"无法读取策略文件 {file.name}，请检查文件是否存在及读取权限") from exc


def install(directory):
    try:
        source = Path(directory).resolve()
        if not source.is_dir():
            raise ValueError("策略路径必须是存在的目录")
    except OSError as exc:
        raise ValueError("无法访问策略目录，请检查路径和读取权限") from exc
    manifest = json.loads(read_source_file(source / "manifest.json"))
    if not isinstance(manifest, dict):
        raise ValueError("模型清单必须为 JSON 对象")
    for key in ("id", "version", "api_version", "entrypoint", "parameters", "lookback"):
        if key not in manifest:
            raise ValueError(f"清单缺少 {key}")
    if any(not isinstance(manifest[key], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", manifest[key]) for key in ("id", "version")):
        raise ValueError("模型 ID/版本只能使用字母、数字、点、下划线或横线")
    validate_manifest(manifest)
    if not isinstance(manifest["entrypoint"], str) or manifest["entrypoint"].count(":") != 1:
        raise ValueError("入口格式必须为 strategy.py:Strategy")
    entry, class_name = manifest["entrypoint"].split(":")
    entry_file = (source / entry).resolve()
    if not entry_file.is_relative_to(source) or entry_file.suffix != ".py":
        raise ValueError("Python 入口必须位于模型目录内")
    tree = ast.parse(read_source_file(entry_file))
    if class_name not in {n.name for n in tree.body if isinstance(n, ast.ClassDef)}:
        raise ValueError("清单入口类不存在")
    dependencies = manifest.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(isinstance(d, str) for d in dependencies):
        raise ValueError("dependencies 必须为依赖名称/版本约束数组")
    for dependency in dependencies:
        requirement = Requirement(dependency)
        if requirement.url:
            raise ValueError("模型依赖只接受已安装的包名/版本约束")
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            installed = importlib.metadata.version(requirement.name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ValueError(f"缺少依赖：{requirement.name}，安装阶段不自动联网执行") from exc
        if installed not in requirement.specifier:
            raise ValueError(f"依赖版本不兼容：{requirement.name}")
    try:
        digest = digest_directory(source)
    except OSError as exc:
        raise ValueError("无法读取完整策略包，请检查文件和读取权限") from exc
    target = ROOT / "runtime" / "strategies" / manifest["id"] / (manifest["version"] + "-" + digest[:16])
    archive_directory(source, target, digest)
    db.execute("INSERT INTO strategies(id,version,digest,manifest,source_path) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
               (manifest["id"], manifest["version"], digest, db.jb(manifest), str(target)))
    return {"id": manifest["id"], "version": manifest["version"], "digest": digest}


def validate_parameters(manifest, supplied):
    definitions, parameters = manifest["parameters"], {}
    if not isinstance(supplied, dict):
        raise ValueError("模型参数必须为对象")
    if set(supplied) - set(definitions):
        raise ValueError("存在清单未定义的参数")
    for key, spec in definitions.items():
        value = supplied.get(key, spec.get("default"))
        expected = spec.get("type")
        if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f"{key} 必须为整数")
        if expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ValueError(f"{key} 必须为数值")
        if isinstance(value, (int, float)) and not math.isfinite(value):
            raise ValueError(f"{key} 必须为有限数值")
        if expected == "boolean" and not isinstance(value, bool):
            raise ValueError(f"{key} 必须为布尔值")
        if expected == "string" and not isinstance(value, str):
            raise ValueError(f"{key} 必须为字符串")
        if "minimum" in spec and value < spec["minimum"] or "maximum" in spec and value > spec["maximum"]:
            raise ValueError(f"{key} 超出允许范围")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"{key} 不在允许值内")
        parameters[key] = value
    return parameters


def load(version):
    path = Path(version["source_path"])
    if digest_directory(path) != version["digest"]:
        raise ValueError("策略文件摘要变化，拒绝改变历史运行输入")
    entry, name = version["manifest"]["entrypoint"].split(":")
    package_name = "stock_strategy_package_" + version["digest"]
    # 每个模型版本使用独立包名；一个运行进程只加载自己的不可变目录。
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    if package_name not in sys.modules:
        initializer = path / "__init__.py"
        if initializer.exists():
            spec = importlib.util.spec_from_file_location(package_name, initializer, submodule_search_locations=[str(path)])
            package = importlib.util.module_from_spec(spec)
            sys.modules[package_name] = package
            spec.loader.exec_module(package)
        else:
            package = ModuleType(package_name)
            package.__package__, package.__path__ = package_name, [str(path)]
            package.__spec__ = importlib.machinery.ModuleSpec(package_name, loader=None, is_package=True)
            package.__spec__.submodule_search_locations = package.__path__
            sys.modules[package_name] = package
    parts = list((path / entry).resolve().relative_to(path.resolve()).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    module = importlib.import_module(".".join([package_name, *parts]))
    return getattr(module, name)

