"""D-05 专用生成说明；完整算法只能由当前题模型生成。"""
import json


def source_prompt(case,materials):
    return (
        '按当前公开材料实现Python3.9标准库run_experiment(request)，只返回一个完整python代码块。'
        '不得联网、读写文件、使用第三方库、反射或打印。不要输出答卷或指标，返回真实逐任务轨迹。'
        '目标约70至100行、1800至2200 token。基线与候选调用同一个仿真函数，只在选等待任务的优先级上不同。'
        '逐条落实以下事件顺序，不能为了短代码遗漏守恒或时间条件：\n'
        '1. 每次调用从空状态开始。任务按(arrival_ms,id)排序，flow_weights包含零服务流；保留小数时间和正权重。\n'
        '2. 时钟从0开始，只有now<horizon才处理。先释放完成服务端、纳入全部arrival<=now任务，'
        '再把deadline<=now的等待任务逐一记入dropped，reason=deadline；不静默过滤。\n'
        '3. 按服务器编号升序；只有free_at<=now才允许派发，忙服务器不能接新任务。'
        '每个空闲服务器内部反复选任务，直到成功派发或等待队列空。FIFO按arrival,id；'
        '候选按(累计已分配service_ms/flow_weight,arrival,id)，不是任务数/已完成服务/setup开销。\n'
        '4. 上次流在服务器空闲期间仍保留。首次无setup，换流才有setup。start=now+setup，end=start+service。'
        '先判start>=deadline丢deadline，再判end>horizon丢horizon；end==horizon可完成。'
        '丢弃不改变free_at、last_flow或累计量，且同一服务器立即尝试下一个任务。\n'
        '5. 成功派发立即增加该流的已分配service_ms，更新free_at和last_flow，再处理下一服务器。'
        'trace保留原任务字段，加server/dispatch_ms/setup_ms/start_ms/end_ms/wait_ms；served_ms累计成功任务的service_ms。\n'
        '6. 本时刻全部派发完后再推进。下一事件只从严格大于now的忙服务器free_at和下一任务arrival中取最小值。'
        '绝对不能对包含空闲服务器旧时刻的整个free_at取min：它会停在过去并提前退出。'
        '等待队列为空但未来还有任务时，必须跳到下一次到达，不能提前break。无未来任务且无等待任务可结束。\n'
        '7. 到窗口后所有未处理任务（包括未到达的）统一dropped/horizon；每任务必须恰好出现一次。'
        'trace按(dispatch_ms,server)排序，dropped按id排序；served_ms含全部声明流，空任务也返回所有流的0。\n'
        '返回{baseline:FIFO结果,candidate:公平结果,details:{}}；每个结果含trace,served_ms,dropped。'
        '不修改request或复用跨调用状态。确实无法完成候选可返回null，但不能复制FIFO冒充。\n'
        f'题号：{case.case_id}\n题目：{case.input}\n公开method：{materials["method"]}\n'
        + 'protocol：'+json.dumps(json.loads(materials['protocol']),ensure_ascii=False,separators=(',',':'))
        + '\nsample：'+json.dumps(json.loads(materials['sample']),ensure_ascii=False,separators=(',',':'))
    )
