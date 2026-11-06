"""Archive this round's source, failures, authorizations and release checks."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parent.parent
REPO=ROOT/'协同开发/research-agent-d-contract-20261106'
REF='2e6f51a643acf66920612f83555a1c3a59d9d8b8'
DELIVERY=ROOT/'交付物'/('2026-11-06-d30-'+REF[:12])
ROUND=REPO/'team_share/rounds/2026-11-06-d30'
manifest=json.loads((DELIVERY/'manifest.json').read_text())
assert manifest['source_commit']==REF and manifest['image_contract_tests']==10
package=Path(manifest['package_evidence'])
assert json.loads((package/'summary.json').read_text())['gate_passed']
ROUND.mkdir(parents=True,exist_ok=True)

def copy(source,relative):
    destination=ROUND/relative
    assert destination.resolve().is_relative_to(ROUND.resolve())
    destination.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,destination)
    assert hashlib.sha256(source.read_bytes()).digest()==hashlib.sha256(destination.read_bytes()).digest()

for path in sorted((REPO/'docs/testing/2026-11-06-d-contract').iterdir()):
    if path.is_file():copy(path,Path('raw/d30-closure')/path.name)
for dirname,group in [('academic-linux-20261105-11c45136','legacy-30-failures'),
                      ('d-contract-20261106-bdd77c7e','native-pairs-and-first-full'),
                      ('d-contract-20261106-e1f6468a','codex-offline-full')]:
    source=ROOT/'镜像测试/runs'/dirname
    for path in sorted(source.iterdir()):
        if path.is_file() and path.suffix in ('.json','.xml','.stdout','.stderr') and path.name!='native-proof.json':
            copy(path,Path('raw')/group/path.name)
for path in sorted(package.iterdir()):
    if path.is_file():copy(path,Path('raw/image-package')/path.name)
for filename in ['prepare_d_rca_20261106.py','prepare_d_guards_20261106.py',
                 'close_d_contract_20261106.py','run_d_contract_offline_20261106.py',
                 'dispatch_score_native_20261102.py','send_score_native_20261102.ps1',
                 'send_native_verified_click_20261106.ps1','archive_d30_release_20261106.py']:
    copy(ROOT/'.session-audit'/filename,Path('tools')/filename)
copy(ROOT/'镜像测试/d_contract_20261106.py',Path('tools/d_contract_20261106.py'))
copy(ROOT/'镜像测试/package_d30_20261106.py',Path('tools/package_d30_20261106.py'))
copy(ROOT/'AGENTS.md',Path('workspace-AGENTS.snapshot.md'))
for filename in ['d-contract-offline-authorization-20261106.json','d-contract-native-limit-20261106.json',
                 'd30-pr-receipt-20261106.json']:
    copy(ROOT/'.session-audit'/filename,Path('receipts')/filename)
copy(REPO/'docs/2026-11-06-D30诊断与验证矩阵.md',Path('D30闭环报告.md'))
copy(DELIVERY/'manifest.json',Path('submission/manifest.json'))
copy(DELIVERY/'SHA256SUMS.txt',Path('submission/SHA256SUMS.txt'))
copy(DELIVERY/'environment.json',Path('submission/environment.json'))
timeline=[
    {'stage':'01-diagnosis','commit':'8cf1963','result':'30 failures recorded before changes; raw baseline retained'},
    {'stage':'02-contract-fixtures','commit':'6e0fc45','result':'current formal contract and cancellation fault injection'},
    {'stage':'03-single-factor-source-fix','commit':'24a222b','result':'two D-only cancellation checkpoints'},
    {'stage':'04-pinned-candidate','commit':REF,'result':'BE/shared runtime preserved'},
    {'stage':'05-native-service-rejection','request_id':'6f7244a532724127a108d51ac882725b','result':'400 before tools: output request 32768 exceeded service limit 12288; not a D test result'},
    {'stage':'06-native-execution','request_id':'abab5d91016d44edb14dbfa4df55bcc9','tool_seq':3925,
     'result':'old/new each three rounds; old each 2 cancellation failures, candidate each 5 passes; full 635 pass/1 missing-fixture failure'},
    {'stage':'07-explicit-offline-exception','user_reply':'已允许','scope':'D zero-model fault injection and Linux regression'},
    {'stage':'08-offline-linux-full','result':'636 pass, 0 failure/error/skip; exact historical prompt fixture supplied; native pairs reused only after source/material/environment/JUnit verification'},
    {'stage':'09-repair-review','url':'https://github.com/Sunming1126/research-agent/pull/16','result':'open, not merged'},
    {'stage':'10-package','result':'exact image source, base SDK, module origin, Linux structure, 10 health checks, 10 image contracts, tar save/load all passed',
     'image_sha256':manifest['sha256']},
    {'stage':'11-github-archive','branch':'archive/d30-release-20261106','result':'source and complete evidence, image binary linked as Release asset'},
]
(ROUND/'timeline.json').write_text(json.dumps(timeline,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
template={'source_commit':REF,'archive_sha256':manifest['sha256'],'image_id':manifest['image_id'],
          'submission_id':None,'eval_id':None,'platform_health':None,'automatic_score':None,
          'per_case_scores':{},'tokens':None,'elapsed_seconds':None,'official_baseline':'eval-20261103-f8116b95',
          'official_baseline_score':80.9,'causal_comparison_valid':False}
(DELIVERY/'评测绑定模板.json').write_text(json.dumps(template,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
copy(DELIVERY/'评测绑定模板.json',Path('submission/评测绑定模板.json'))
instructions=f'''# 本轮镜像提交说明

上传文件：{Path(manifest['archive']).name}

SHA256：{manifest['sha256']}

大小：{manifest['bytes']} 字节。源码固定提交：{REF}。镜像ID：{manifest['image_id']}。

## 本地文件

直接向比赛平台上传本目录的.tar文件。manifest.json、SHA256SUMS.txt与评测绑定模板是留存核对材料，不替代镜像tar。上传后将submission_id、eval_id填写到模板，绑定这次SHA256；先等待平台结构与health通过，再按平台正常流程评测。

本版保留源码AGENT_VERSION=2.0.10-bd7；为避免版本混淆，以本文件名、SHA256和镜像revision标签识别本轮D30镜像，不凭页面版本字符串判断是否同一份文件。

## 已核验

- linux/amd64、非root agent用户、官方AgentRuntime入口、8080端口与启动命令。
- 59份候选Python文件及4份基础镜像继承文件逐字节核验，原SDK保持；排除旧题算法夹具，未包含本机连接配置或凭据。
- 本地health连续10次HTTP 200；新镜像内5项取消/超时、2项未修改官方客户端Unix RPC、3项Linux隔离检查，合计10项全过，0跳过。测试用例导入的生产模块来自/app/src。
- 源码完整Linux回归636通过、0失败/错误/跳过；原Harness新旧各三轮配对证据保留。
- tar已导出并重新load，镜像ID一致。

## 结论边界

本轮没有启动新正式评测，不保证高于80.9分；D-02第二主场景与D-05服务量统计未宣称算法修复。平台health仍待上传后的平台检查。本次仅准备材料，未替你点击正式评测。

完整源码、过程与失败证据分支：archive/d30-release-20261106。修复PR：https://github.com/Sunming1126/research-agent/pull/16 。镜像下载链接见同目录GitHub下载地址.md（上传完成后生成）。
'''
(DELIVERY/'提交说明.md').write_text(instructions,encoding='utf-8')
copy(DELIVERY/'提交说明.md',Path('submission/提交说明.md'))
readme='''# 2026-11-06 D30完整归档

本分支是本轮保存分支，不覆盖main或队友codex/friend-development，也未合并PR #16。

- D30闭环报告.md：观察—假设—实验—结论—决策，包含提分未验证的边界。
- timeline.json：修复前预登记、30项迁移、生产取消修复、原生400、首次全量失败、离线授权和复测、镜像检查的时间顺序。
- raw/legacy-30-failures：最初601/30与队友554/30原始结果。
- raw/native-pairs-and-first-full：新旧各3轮以及635/1缺失挂载的失败。
- raw/codex-offline-full：获明确允许后的636/0/0/0实测；没有冒充原总控。
- raw/image-package：新镜像的构建、来源、health、镜像内契约、导出/回读日志。
- tools：本轮实际使用的协调和打包入口归档；这些入口依赖原目录、冻结材料及执行来源限制，不能脱离上下文冒充新的Harness执行。
- receipts：D无模型执行授权、单字段服务参数调整与PR回执；不含完整连接配置、密钥或浏览器登录状态。
- submission：镜像清单、SHA256、提交说明、空白正式评测绑定模板；大文件通过GitHub Release附件保存。
- archive-sha256.json：原始文件逐字节校验清单。

生产源码固定2e6f51a643acf66920612f83555a1c3a59d9d8b8；本归档包含之后的证据和交付文档，新增提交号不表示又优化了算法。正式分数和Delta为空，D02/D05算法失分不因回归通过而被标为已解决。
'''
(ROUND/'README.md').write_text(readme,encoding='utf-8')
files={p.relative_to(ROUND).as_posix():{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}
       for p in sorted(ROUND.rglob('*')) if p.is_file() and p.name!='archive-sha256.json'}
(ROUND/'archive-sha256.json').write_text(json.dumps(files,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'archive_directory':str(ROUND),'files':len(files),'submission':str(DELIVERY)},ensure_ascii=False))

if __name__=='__main__':pass
