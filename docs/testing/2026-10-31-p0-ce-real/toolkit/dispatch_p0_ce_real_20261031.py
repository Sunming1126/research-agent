"""Send one coordinator command to the visually verified original Harness chat."""
import argparse
import hashlib
import json
import sys
import time
import subprocess
import shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'镜像测试'))
from native_workspace_harness import workspace,selection,native_rows
from harness_window import focus,point,chord,type_text,window,user32
from ctypes import wintypes
import ctypes
SID='session-9c903d0e-5884-44fe-bc7f-3719da00864b'
WID='c61ec5e8-9932-4ef5-9709-fed302ad7355'
parser=argparse.ArgumentParser();parser.add_argument('--stage',choices=['precheck','pair'],required=True)
parser.add_argument('--round',type=int,required=True);args=parser.parse_args()
prepare_only='P0_CE_PREPARE_UI_ONLY' in __import__('os').environ
meta=json.loads((ROOT/'.session-audit/p0-ce-real-study-20261031.json').read_text(encoding='utf-8'))
study=meta['study'];directory=Path(meta['directory'])
if args.stage=='precheck' and (directory/'precheck-result.json').is_file():raise RuntimeError('Precheck already executed; do not duplicate')
if args.stage=='pair' and (directory/f'pair-{args.round}-result.json').is_file():raise RuntimeError('Pair already completed; do not duplicate')
selection();record=workspace();assert SID in record['sessionIds'];focus()
rect=wintypes.RECT();user32.GetWindowRect(window(),ctypes.byref(rect));assert (rect.right-rect.left,rect.bottom-rect.top)==(1941,1243)
rows,_=native_rows(SID);before=max(r.get('seq',0) for r in rows)
configurations=[r['data']['header']['config'] for r in rows if r.get('type')=='request/header']
assert configurations[-1]['provider']=='magic'
proof={'study':study,'stage':args.stage,'round':args.round,'session_id':SID,'workspace_id':WID,'before_seq':before,
       'ui_binding_sha256':hashlib.sha256((directory/'native-ui-before-call.png').read_bytes()).hexdigest(),
       'verified_original_native_ui':True,'registered_session_ids_before':sorted(record['sessionIds']),
       'created_unix_seconds':time.time(),'new_conversations_created':0}
request=ROOT/'.session-audit/p0-ce-real-requests-20261031'/f'{study}-{args.stage}-{args.round}.json'
request.parent.mkdir(parents=True,exist_ok=True);request.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
command=("& 'C:/Users/lenovo/AppData/Local/Programs/Python/Python314/python.exe' "
         "'C:/Users/lenovo/Desktop/Agent挑战赛/镜像测试/p0_ce_real_20261031.py' --study "
         +study+' --stage '+args.stage+' --round '+str(args.round))
message=("P0真实模型验证。用户已切回当前Agent挑战赛/科研助手镜像验收总控，并授权继续C/E测试。"
 "Codex已将四项修复依次离线对照并完成439通过、6环境跳过、1已知xfail，P0尚未整体验收。"
 "请只在当前原生工具中执行下面这一条协调器命令并等待同一进程结束，不重复启动： "+command+" 。"
 "工作目录C:/Users/lenovo/Desktop/Agent挑战赛。协调器会验证本会话新鲜工具调用，才调用既有magic；"
 "只从本机既有连接在内存使用凭据，禁止输出凭据、配置或上游原始错误。"
 "本阶段precheck只做两题候选真实预检；pair按预先冻结的四题新旧交替，题目和五篇公开论文哈希不变，"
 "单题180秒/40000Token、最多30调用，额度耗尽或候选硬失败立即停止，所有失败保留。"
 "使用官方AgentRuntime及SDK工具/真实LLM埋点，但这是Windows本地真模型诊断，不是Linux/平台成绩。"
 "不要修改任何源码、用例、阈值、工作区、模型设置或审批策略；不要创建新会话/工作区、打榜或合并GitHub。"
 "若原生权限要求审批，请正常申请并等待用户，不代点。执行后报告实际阶段状态、HTTP/落盘/追踪与调用Token，"
 "不要把格式合格称为事实正确。输出路径由脚本写入.session-audit/p0-ce-real-phase-latest-20261031.json。")
(directory/f'native-{args.stage}-{args.round}-prompt.txt').write_text(message,encoding='utf-8')
if prepare_only:
    print(json.dumps({'prepared_only':True,'prompt_path':str(directory/f'native-{args.stage}-{args.round}-prompt.txt'),
                      'target_model_calls':0}));raise SystemExit(0)
result=subprocess.run([shutil.which('pwsh'),'-NoProfile','-STA','-File',str(ROOT/'.session-audit/paste_p0_native_prompt_20261031.ps1'),
                       '-Stage',args.stage,'-Round',str(args.round),'-Send'],capture_output=True,timeout=30)
if result.returncode:
    raise RuntimeError('Native input/send control failed; inspect the existing editor without resubmitting')
receipt=json.loads(result.stdout.decode('utf-8-sig'))
assert receipt['submitted'] and receipt['input_matches']
(directory/f'native-{args.stage}-{args.round}-dispatch.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
for _ in range(60):
    rows,_=native_rows(SID)
    if any(r.get('seq',0)>before and r.get('type')=='user/message' and study in json.dumps(r,ensure_ascii=False) for r in rows):
        assert sorted(workspace()['sessionIds'])==proof['registered_session_ids_before']
        print(json.dumps({'submitted':True,'study':study,'stage':args.stage,'round':args.round,
                          'session_id':SID,'before_seq':before,'new_conversations_created':0}));break
    time.sleep(.2)
else:raise RuntimeError('No native message acknowledgement; do not blindly resend')
