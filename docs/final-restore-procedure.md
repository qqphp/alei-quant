# 最终覆盖与恢复核验执行顺序

2026-10-11已实际完成以下流程：最终版本33633，备份 `runtime/backups/20261011-143926-574371`，恢复库 `stock_alei_restore_20261011_144224_4c9165`。三项核验均成功退出并读回结果：工具内旧值及原报告重算、最新交付恢复、全量源值及当前三报告重算。证据见 [交付恢复](verification-latest-delivery-restore.json)、[全部源值恢复](verification-final-source-restore.json)及[最终服务](verification-final-runtime-readback.json)。以下保留可重复执行的流程。

四项审计及两项恢复核验工具均放在会随备份复制的 `scripts` 目录。三个覆盖工具和最新交付恢复工具从已运行的临时版本逐字复制，SHA-256相等；本次最终执行使用下列持久路径，旧临时文件保留其历史来源。

## 前置条件

在原工作区 `E:\codex\stock` 执行，使用本机 `.venv` 与已配置的应用角色。原全市场任务必须31346/31346完成，额外任务878/878完成。固定最终版本后，先完成以下四项只读审计；全部使用同一个版本，不能直接混用进行中生成的不同版本文件。

```powershell
$env:PYTHONPATH = 'backend'
$env:PYTHONUTF8 = '1'
# 把下面四处 <版本号> 替换为同一个实际发布版本。
.venv/Scripts/python.exe scripts/audit-collection-operation-coverage.py --version <版本号>
.venv/Scripts/python.exe scripts/audit-daily-member-coverage.py --version <版本号>
.venv/Scripts/python.exe scripts/audit-action-source-integrity.py --version <版本号>
.venv/Scripts/python.exe scripts/audit-source-row-integrity.py --version <版本号>
```

每项都须成功退出，并读回JSON内容核对。请求完成与原行摘要正确不证明没有经济数据缺口；保留实际价格缺失分类、空响应、非价格标记和仅采集事件。

## 备份与独立恢复

1. 更新最终数据范围及来源限制说明，稳定当前代码/文档/模型状态，然后执行 `scripts/backup.ps1`。备份完成的目录以实际命令输出为准；不覆盖已有备份。
2. 对该目录执行 `.venv/Scripts/python.exe -m stockapp.maintenance restore-check --directory '<实际备份目录>'`。管理员密码在本机提示中输入；不放进文档。工具创建独立恢复库，保留主库；既有验证价格修订明确标记且仅在副本。
3. 执行 `.venv/Scripts/python.exe scripts/verify-latest-delivery-restore.py '<实际备份目录>'`，核对程序/构建文件、模型启用状态、参数研究、九份报告、三项完整归档重算，以及全部报告/任务/日志HTTP分页。
4. 执行 `.venv/Scripts/python.exe scripts/verify-final-source-restore.py '<实际备份目录>'`，核对四项共同版本审计，逐行比较全部固定版本发布源记录、批次与池快照的所有列；逐文件核对说明和正式原件；完整重算当前570日经典策略及迈瑞两个账本报告。
5. 读回两个验证结果及恢复库运行状态。补充验收生成在备份根目录的 `restore-latest-delivery-verification.json`、`restore-final-source-verification.json` 是快照后的验证附件，不假称它们已包含在先前数据库dump中。

最终备份的 `backup.json.files_sha256` 保留复制时每个源文件与副本已实际比较相等的摘要；恢复时先核对整个清单。原件和快照中的旧验收记录不改写；新生成的最新恢复证据另记其原/新摘要、实际目标备份及恢复库。其它说明或原件的差异不能作为“生成新证据”忽略。

完整结果与冻结清单相等、固定版本原值相等、后台验证API已停止，已据此更新最终完成声明。恢复后生成的证据及中文完成说明保存到备份 `post-verification` 目录，附件的独立SHA清单与原 `backup.json.files_sha256` 分开；原快照文件不改写。此前11:05等备份的真实验收继续保留其原范围；未声称新机器依赖重装或任意历史报告全部重算。
