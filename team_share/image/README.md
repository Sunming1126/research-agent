# 镜像文件领取与核对

2026-10-24 核对远程 main `64f5efd881d41b24354d6e82f02b450c799e48ff`：本目录在 Git 中只有七个 `.tar.sha256` 校验文件，没有这些版本对应的 `.tar` 本体，也没有 LFS 指针。

校验文件约 100 字节，内容是摘要与文件名，不能作为 Docker 镜像导入或向赛事上传。本地文件是否存在与远程文件是否已经上传，需要分别核对。当前 `.gitignore` 的 `/team_share/image/*.tar` 会排除本目录的 tar；仅运行 `git lfs track` 不会自动把已忽略的 tar 加入提交。

| 本目录版本 | 远程 main 校验文件 | 本目录镜像本体 |
| --- | --- | --- |
| 0.4.1-v14-20261023 | 存在 | 未上传到此目录；另有已发布 Release |
| 0.4.1-v14-20261024 | 存在 | 未上传到此目录 |
| 0.4.2-http-20261024 | 存在 | 未上传到此目录 |
| 0.4.3-runtime-20261024 | 存在 | 未上传到此目录 |
| 0.4.4-contract-20261024 | 存在 | 未上传到此目录 |
| 0.4.5-official-20261024 | 存在 | 未上传到此目录 |
| 0.4.6-team-20261024 | 存在 | 未上传到此目录 |

0.4.6-team-20261024 的预期 SHA-256 为 `1add15c65ea04c2eb42aa931a035a04194b251714a4969f9ccd4bd672040ec79`；这是待取得归档后的比对依据，尚未验证本地实物。

已实际存在的历史发布包括 [v14 镜像](https://github.com/Sunming1126/research-agent/releases/tag/v0.4.1-v14-20261023) 与 [hybrid v8 镜像及证据](https://github.com/Sunming1126/research-agent/releases/tag/v0.4.2-hybrid-v8-20261023)。二者均不是新的 0.4.6 或 0.4.7，不能替代最新版准入核验。

约 295 MB 的归档可作为 GitHub Release 附件交接，源码、说明和校验值继续走 Git。也可以使用正确提交并成功推送的 LFS 指针，但必须从接收端取得完整文件并复算摘要后，才算交接完成。GitHub 普通 Git 对大于 100 MiB 的文件有限制，参见[官方大文件说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)。本次没有修改忽略规则、安装 LFS 或改写历史。

接收新版后须记录：实际归档名/大小/SHA-256、镜像 ID/标签、对应源码提交；之后再审核官方 AgentRuntime 入口和实际安装源码，并在既有 Harness 工作区固定主对话中执行复测。当前 0.4.7 为未构建、未完成功能验收的源码候选，不是可直接上传的镜像。
