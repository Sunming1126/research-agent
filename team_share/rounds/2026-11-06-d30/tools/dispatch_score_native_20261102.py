"""Send only a bounded, explicit command to the existing native main chat."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'镜像测试'))
from native_workspace_harness import workspace,selection,native_rows
SID='session-9c903d0e-5884-44fe-bc7f-3719da00864b'
parser=argparse.ArgumentParser();parser.add_argument('--stage',required=True,choices=['baseline','baseline-path','candidate','ce-regression','full-regression','sdk','time-paired','e2e-full','e2e-d02','e2e-d02-prompt','platform-canary','d05-diagnosis','academic-regression','academic-be-pair','academic-e-pair','academic-b-score','academic-e-three','academic-linux','academic-score-preflight','academic-score-final','be-focus','be-release','be-platform','be-submit', 'd-contract']);parser.add_argument('--factor',default='ce',choices=['ce','time','e2e']);parser.add_argument('--candidate');args=parser.parse_args()
pin_candidate = args.stage in {'e2e-d02-prompt', 'academic-regression', 'academic-be-pair', 'academic-e-pair',
                               'academic-b-score', 'academic-e-three', 'academic-linux', 'academic-score-preflight', 'academic-score-final', 'be-focus', 'be-release', 'be-platform', 'be-submit', 'd-contract'}
if pin_candidate and not re.fullmatch(r'[0-9a-f]{40}', args.candidate or ''):
    parser.error('This comparison requires an exact candidate commit')
selection();record=workspace();assert SID in record['sessionIds']
rows,_=native_rows(SID);assert rows[-1]['type'] in ('turn/end','session/end-seed')
request_id=uuid.uuid4().hex
folder=ROOT/'.session-audit/score-native-requests-20261102';folder.mkdir(parents=True,exist_ok=True)
command=("& 'C:/Users/lenovo/AppData/Local/Programs/Python/Python314/python.exe' -X utf8 "
         "'C:/Users/lenovo/Desktop/Agent挑战赛/镜像测试/score_compat_20261102.py' --stage "+args.stage+' --factor '+args.factor+' --native-request '+request_id)
prompt=('继续用户授权的正式规则兼容优化，代码基于队友codex/friend-development的cd733b2，当前独立协作分支improve/score-compat-20261102。'
        'C/E第一因素已完成23项旧新对照12/23→23/23、182专项及12原生SDK合成HTTP通过；全量480通过/1原版同样存在的Windows路径失败/6条件跳过。不是官方分数。'
        '请只在当前Agent挑战赛／科研助手镜像验收总控原生工具中执行一次下列命令，工作目录C:/Users/lenovo/Desktop/Agent挑战赛：'
        +command+' 。协调器核验本次新鲜原生工具调用后才运行。'
        '本阶段只是无目标模型的源码/官方SDK合成HTTP回归，外连禁用护栏保留；不读密钥，不调用目标模型。'
        '不要修改源码、测试、阈值、模型、工作区、会话、审批策略、Git状态或重建镜像；不要重复启动。'
        'time-paired阶段只验证单因素限时兼容：已提交R1作为旧版基线、当前候选，固定交替顺序各3次33项时钟/HTTP/取消/D客户端无模型控制，然后完整回归和SDK；所有子阶段在同一有界协调器中串行执行，不修改源码或算法。'
        '请按原生工具正常申请本命令的一次性批准访问后执行，不更改审批策略、不代点、不在探针失败后循环重跑。等待同一进程完成，报告实际结果与失败，不把静态或离线结果当平台验收。')
if args.stage in ('e2e-full','e2e-d02'):
    prompt=('用户最新明确要求立即真实目标模型端到端验证，不再停在离线回归。请在当前固定Agent挑战赛／科研助手镜像验收总控原生工具执行一次：'+command+
        '。本次将实际使用既有magic连接，由原生协调器发起有独立请求/响应/Token证据的真实目标调用，不是单元测试或合成响应。'
        '顺序先同输入比较队友cd733b2与当前95b8e6d的D05模型源码生成和公开执行，再完成候选11题公开材料E2E；A/B/C/E走未改官方AgentRuntime HTTP、落盘回读，D走真实模型源码和公开/派生输入自检。'
        '当前还没有平台私有题和裁判，因此不得称本地分为官方分，不得造分或把缺正式D RPC写成通过；原始失败全部保留。'
        '请先按原生工具申请本命令网络与本地HTTP/子进程的一次性正常批准，再执行。模型凭据只由现有magic内存桥读取，不在聊天或日志输出；不得更改模型、工作区、会话、审批策略或源码。'
        '允许此有界任务运行至完成并等待同一进程，不重复启动，不在失败后自行改代码；额度耗尽立即停。')
    if args.stage=='e2e-d02':
        prompt+=' 本轮实际是D02单因素新旧交替对照：旧95b8e6d与新089e657，旧→新→新→旧共4次同一真实模型源码生成/公开执行。新策略只关闭D02推理，算法要求和4200输出/40000总Token不扩；此前旧版两次都4200推理Token耗尽、零代码。不要重复跑11题。'
if args.stage=='platform-canary':
    prompt=('用户已明确要求真实打榜E2E与官方Score。当前2.0.2-score-canary镜像通过网页正常上传中，源码含规则/限时/路径修复，以及真实D02对照有效的关闭推理策略。请在原固定总控原生工具执行一次：'+command+
        '。此协调器只通过已登录网页正常控件等待上传与检查，不读取浏览器存储/令牌，不调用隐藏或管理员接口。'
        '平台当前已有队友评测eval-20261102-975b53cf运行，绝不取消或重复提交；等它完成且本次镜像READY后才点击一次赛题一开始打榜评测，继续读取新评测官方分数与相对73.9分Delta，保留状态日志。'
        '这是用户明确授权的正式评测，会消耗平台模型额度；不得把公开样例分当官方分或编造排名。不得改源码/测试/模型/工作区/会话/审批策略。'
        '请通过原生工具正常申请本命令GUI访问的一次性批准。该任务可等待至110分钟，工具超时请足够覆盖；只等待同一进程，不重启，不代点审批。自动审批先前拒绝的浏览器令牌提取方式已停止，绝不可使用。')
if args.stage=='d05-diagnosis':
    prompt=('继续用户明确要求的D-05专项根因诊断。验证矩阵已提交协作分支，生产源码保持不变。请只在当前固定Agent挑战赛／科研助手镜像验收总控原生工具执行一次：'+command+
        '。本次零目标模型调用，只回放两份已保留的真实模型源码，运行断网/只读根/1CPU/1GiB的Python3.9 Linux容器，覆盖公开样例、6公开边界、seed42组合、规模和扩展字段探针。'
        '扩展字段与规模探针不等于正式题面，不能据此造分；准确记录resource/syscall实际状态和全部失败。正式平台提交权限当前不可用，本次不提交、不取消任何评测。'
        '按原生工具正常申请本命令Docker/文件访问的一次性批准后再执行；不修改源码、阈值、凭据、模型、会话、工作区、审批策略或Git状态，不重复启动，等待同一进程结束。'
        '仅原协调器调用新的d05_diagnosis_20261103.py；无需任何模型凭据。不得将这些公开/Linux回放算作正式验收。')
if args.stage == 'e2e-d02-prompt':
    prompt=('用户要求优化后提交一版。请在当前原生总控工具执行一次 '+command+
            ' 。固定公开D-02输入，按旧、新、新、旧运行同一个magic模型。旧为封存2.0.2源码51e9514，'
            '新为 '+args.candidate+'，唯一运行源码变化为D-02生成提示去重，保留同一算法要求、Token上限和公开校验。'
            '保留各次原始成功和失败，记录通过情况及真实Token；结果属于公开实验。'
            '只通过原生工具申请必要的正常权限，不改模型、源码、工作区、会话、审批策略或平台镜像，不重复运行。')
if args.stage == 'academic-regression':
    prompt=('用户要求以队友2.0.10-bd7提交046324b为基线验证B-02、D-02、D-05、E-04约束。'
            '请仅在当前原生总控工具执行一次 '+command+' 。候选提交为 '+args.candidate+'。'
            '协调器先以队友源码运行固定反例，再验证候选和相邻既有测试。测试是合成输入，不请求目标模型或平台。'
            '按原生工具申请必要的本地执行权限。等待同一进程结束，保留失败，不修改代码、会话或审批策略，不重跑。')
if args.stage in {'academic-be-pair', 'academic-e-pair'}:
    scope = 'E-04' if args.stage == 'academic-e-pair' else 'B-02、E-04'
    prompt=('用户要求在队友2.0.10-bd7基础上做可核验优化。请在当前固定原生总控执行一次 '+command+
            ' 。协调器先跑无模型的固定反例与既有回归。候选通过后才用既有magic在固定公开题与PDF上进行'+scope+'新旧实测，走官方SDK本地HTTP。'
            '基线046324b，候选 '+args.candidate+'。记录结果和实际Token，保留失败与配额中断。'
            '不启动正式评测，不修改源码、工作区、会话、模型或审批策略，不重复调用。'
            '正常申请本命令一次性权限，工具超时请覆盖约20分钟，等待同一进程。')
if args.stage in {'academic-b-score', 'academic-e-three', 'academic-linux', 'academic-score-preflight', 'academic-score-final', 'be-focus', 'be-release'}:
    scope = {
        'academic-b-score': '用既有magic真实调用，固定公开B-02输入及论文，前版与候选按旧新新旧顺序各跑两次，保存官方SDK本地HTTP与逐篇证据。',
        'academic-e-three': '先跑候选专项回归；通过后用既有magic和官方SDK本地HTTP，固定公开E-04题面与证据，独立运行候选三次，保存完整报告、证据与失败。B已在1d092bb的同运行源码上完成三轮及两个边界对照，本次仅有E专用差异，不重复B或D。',
        'academic-linux': '在Linux容器运行完整回归和三个此前跳过的D类用例，记录环境、测试与失败，不调用模型。',
        'academic-score-preflight': '依次做固定反例回归、用magic对B-02新旧各跑两轮、用Linux跑三项D及完整回归、用magic生成E-04三份报告。D环境失败时仍采集独立E-04实验，平台放行保持关闭。',
        'academic-score-final': '对最终B-02改动用magic新旧各跑两轮，重跑Linux回归和本地镜像结构与/health。E-04三轮沿用同运行源码结果。门禁通过才输出待提交tar。',
        'be-focus': '按用户最新优先级，用scope-v2固定公开题和原论文。B以b0bcbbc为对照，只检验保留否定后件的原题断句；E以484bfa0为对照，只检验E-04写作及修订推理，两边审查推理及其完整JSON预算保持一致。两个对照独立冻结源码，可分别归因。另有允许实际使用检索器的正向边界对照。候选B/E各三轮，通过官方SDK本地HTTP保存报告、逐篇原文和语义审查，先跑专项回归。暂缓D共性失败攻坚。',
        'be-release': '按用户已授权的B/E放行条件检查绑定的真实验收证据，构建精确候选Linux镜像并查结构与health，导出对应tar。只有验证全部通过才准备正常网页提交。不得替换不匹配的源码或镜像。',
    }[args.stage]
    prompt=('按用户最新提分任务，只在当前Agent挑战赛原生总控执行一次 '+command+
            ' 。候选为 '+args.candidate+'。'+scope+
            ' 请正常申请一次性执行权限，工具超时覆盖60分钟。保留全部结果。'
            '不修改代码、模型、会话、工作区、审批策略，也不重复执行或发起平台评测。')
if args.stage == 'be-platform':
    prompt=('用户已明确授权B-02和E-04验收通过后提交一次正式评测，D类共同历史失败后续处理。'
            '请只在原Agent挑战赛／科研助手镜像验收总控原生工具执行一次 '+command+
            ' 。候选 '+args.candidate+'，协调器重新校验release-plan、真实三轮B/E结果和精确tar SHA后，'
            '仅通过正常已登录参赛网页上传该tar，等待平台镜像结构与health通过，再开始一次赛题一正式评测。'
            '等候队友正在运行的评测结束，不取消、不重复启动。记录上传ID、评测ID、官方分和对80.9的Delta。'
            '不读取浏览器存储、令牌或后台接口，不改模型、凭据、代码、工作区、会话或审批策略。'
            '通过原生工具正常申请必要GUI执行权限，工具超时覆盖115分钟，等待同一进程完成。')
if args.stage == 'be-submit':
    prompt=('用户已经明确授权B/E验证通过后提交一次正式评测。请只在当前固定原总控原生工具执行一次 '+command+
            ' 。候选 '+args.candidate+'。协调器先验证已封存B三轮/边界两轮与E三轮质量证据，再构建精确Linux镜像，'
            '核对全部运行源码、官方入口、结构与health、三个D检查和完整回归；三十项共同历史失败只按用户明确延后规则比较标识，不作通过。'
            '通过后导出SHA绑定tar，仅通过正常已登录网页上传，等平台结构与health通过并等队友当前评测结束，再开始一次赛题一正式评测。'
            '不取消、不重复评测；等官方分数，并采集全部逐题得分/可见错误/Token/耗时，对照历史80.9分，未知源码或输入指纹不伪装成因果对照。'
            '不读取浏览器令牌、存储或隐藏接口，不改凭据、模型、源码、会话、工作区或审批策略。'
            '正常申请本命令Docker/GUI的一次执行权限，工具超时覆盖135分钟；这是一次可核验的打包与正式canary，等待同一进程完成。')
if args.stage == 'd-contract':
    prompt=('用户最新明确优先攻坚D类30项共性失败。修复前诊断与30项逐条矩阵已提交独立分支improve/d-contract-20261106。'
            '请只在当前Agent挑战赛／科研助手镜像验收总控原生工具执行一次 '+command+
            ' 。精确候选 '+args.candidate+'，冻结B/E dc9不提交正式评测，不取消队友任务。'
            '本轮无目标模型调用：先用现行同一套契约故障注入，在dc9生产源码和候选间按旧新新旧旧新各三轮交替；'
            '预期旧版第三次取消/取消后迟到回复两例失败，候选应消除，再跑Linux全部回归且不允许skip。'
            '三十项原始失败JUnit完整保留。测试维护与真实生产修复分别归因；只改D专用取消传播，不恢复旧题oracle、95秒或隐式baseline选路。'
            '所有测试在断网1CPU/1GiB Linux容器中使用合成响应和未修改官方客户端；不把通过数写成官方分。'
            '按原生工具正常申请必要Docker与执行权限后执行，超时覆盖30分钟，等待同一进程完成，不重复运行。'
            '不得改源码、测试、阈值、模型、凭据、工作区、会话或审批策略，不代点审批。')
prompt_path=folder/(request_id+'.txt');prompt_path.write_text(prompt,encoding='utf-8')
proof={'request_id':request_id,'session_id':SID,'workspace_id':'c61ec5e8-9932-4ef5-9709-fed302ad7355','stage':args.stage,'factor':args.factor,
       'before_seq':max(r.get('seq',0) for r in rows),'prompt_file':str(prompt_path),'registered_sessions':sorted(record['sessionIds'])}
if pin_candidate:
    proof['candidate_commit'] = args.candidate
(folder/(request_id+'.json')).write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
r=subprocess.run([shutil.which('pwsh'),'-NoProfile','-STA','-File',str(ROOT/'.session-audit/send_score_native_20261102.ps1'),'-RequestId',request_id],capture_output=True,timeout=30)
if r.returncode:
    # Own sender errors have no model/config data; retain locally for diagnosis.
    (folder/(request_id+'-sender-error.txt')).write_bytes(r.stderr)
    raise RuntimeError('Native send stopped; no fallback typing or new chat created')
receipt=json.loads(r.stdout.decode('utf-8-sig'));assert receipt['submitted']
for _ in range(50):
    rows,_=native_rows(SID)
    if any(r.get('seq',0)>proof['before_seq'] and r.get('type')=='user/message' and request_id in json.dumps(r) for r in rows):
        assert sorted(workspace()['sessionIds'])==proof['registered_sessions']
        print(json.dumps({**receipt,'session_id':SID,'new_conversations_created':0}));break
    time.sleep(.2)
else:raise RuntimeError('No original session acknowledgement; do not blindly resend')
