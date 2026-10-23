# 0.4.1 本地验收与队友交接

被测功能提交为 `2cae890ead17666361a46e09c0cee33d68f7660f`，基于 `7ceaaa2`；此目录是随后补交的验收证据，不代表重新取得官方成绩。

已上传：[PR #1](https://github.com/Sunming1126/research-agent/pull/1)、[0.4.1预发布下载](https://github.com/Sunming1126/research-agent/releases/tag/v0.4.1-source-v1)。Release含12个附件，GitHub返回的资产摘要已与本地SHA-256比对，见[发布回执](github-publication.json)。主分支等待队友审查合并。

| 检查 | 结果 | 记录 |
| --- | --- | --- |
| 项目全量离线回归 | 129通过、1跳过、0失败；跳过项需300篇论文库 | [regression.xml](regression.xml) |
| D01/D02/D05公开输入 | Python3.9下13/13通过 | [初版验收明细](live-checks-before-report-fix.json) |
| 三题独立容器重放 | 3/3相同 | [重放记录](reproducibility.json) |
| 最终镜像C/E定向复测 | 两项通过 | [复测记录](final-report-checks.json) |
| C/E原文页码回查 | 50/50摘录在对应PDF物理页找到 | [原文核验](quote-evidence.json) |
| 镜像源码和运行身份 | 34个项目源码文件、入口哈希一致；agent用户、amd64 | [镜像源码审计](image-source-audit.json) |

正常D05真实调用中，第一个生成版本在空闲间隔探针中失败；模型收到上一版完整源码和具体反例后，第二次完整生成通过六项内部探针及七个外部公开输入，见[逐次自测记录](D-05-selftest-history.json)。生成文件未经本地算法修补。

## 必须保留的限制与失败

- 推荐本地Harness配置为`deepseek-account/deepseek-flash`、关闭思考。High模式初轮有截断及流程失败，见[High模式记录](high-mode-summary.json)，不能以关闭思考的结果宣称High已通过。
- [修复前验收](live-checks-before-report-fix.json)的E类报告失败源于多行数字与引用分离；修复后的C/E记录单独保存，没有覆盖失败。
- [历史故障回放](negative-replay.json)成功识别旧卡死，但该次模型修复使用私有运行时属性，被拒绝并按两次上限停止。该次修复未完成，不能算成功。
- 来源签名、官方Gateway审计、隐藏输入与可信指标需要平台环境；`official_score=null`。本地检查通过不保证正式成绩或模型下一次生成一定正确。
- 摘录在原文中存在，不等于所有语义解释及原创性已通过人工评审。C/E有预算不足时使用证据保底正文的路径，仍需队友抽查质量。

## 队友如何复测

完整回归入口：

```powershell
./scripts/run_reliability_checks.ps1 -Image research-assistant:0.4.1-source-v1
```

独立公开算法验证只依赖Docker和主机Python标准库。先准备`python:3.9-slim`，然后在仓库根目录执行：

```powershell
docker pull python:3.9-slim
python scripts/acceptance/verify_public_source.py --case D-01 --source docs/testing/2026-10-23/source-snapshots/D-01-accepted.py
python scripts/acceptance/verify_public_source.py --case D-02 --source docs/testing/2026-10-23/source-snapshots/D-02-accepted.py
python scripts/acceptance/verify_public_source.py --case D-05 --source docs/testing/2026-10-23/source-snapshots/D-05-accepted.py
```

每次运行在`output/acceptance/`创建新目录，记录请求、进程输出和独立校验结果，失败返回非零退出码。历史失败输入可对`D-05-rejected-attempt-1.py`执行相同命令验证。来源快照只供离线复现，**不能当成正式评测当前会话的生成源码**；正式路径必须重新调用当前题Gateway生成。

公开校验器位于`scripts/acceptance/public_oracles.py`，仅由外部测试协调器使用。它和本目录快照均不会进入Agent镜像，不能发送给被测模型充当实现。

## 镜像与源码交付

最终镜像为`research-assistant:0.4.1-source-v1`，镜像tar约282 MiB；[交付清单](artifact-manifest.json)和[SHA256SUMS](SHA256SUMS.txt)记录校验值。大文件使用GitHub Release资产分发，不加入Git历史。镜像加载：

Release上的小文件使用唯一英文名，下载后按`RELEASE-SHA256SUMS.txt`核对；原`SHA256SUMS.txt`保留构建快照的中文原始文件名校验值。镜像和源码大文件名保持不变。

```powershell
Get-FileHash research-assistant-0.4.1-source-v1.tar -Algorithm SHA256
docker load -i research-assistant-0.4.1-source-v1.tar
```

源码压缩包是功能提交2cae890的构建快照；本仓库还包含随后提交的验收证据与复测脚本。原始完整模型日志保留在测试机桌面与D盘，原生会话已加入Harness目录。本目录使用相对工作区路径，不要求队友存在相同的用户目录。

建议队友先审查D类探针与修复边界，再抽查C/E语义质量，最后在正式平台验证来源准入。后续优化按独立分支、测试记录及PR同步，保留失败结果，避免直接覆盖他人的主分支工作。
