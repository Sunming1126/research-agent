"""固定公开论文、自拟复杂条件题；在运行前冻结期望文件集合，不是官方金标。"""
import hashlib
import json
import sys
from pathlib import Path
import run_official_live_audit as runner

runner.ROOT=Path(r'C:/Users/sun/Desktop/anic')
ORIGINAL_SPECIMENS=runner.specimens
QUESTIONS=[
 ('B-01','查找研究开放域问答、并在本篇实际使用稠密段落检索的原创方法论文。两个条件须同时满足。不要仅因相关工作提到某方法就纳入。尽量找全，不限定篇数。', ['2004.04906-DPR.pdf','2005.11401-RAG.pdf','2007.01282-FiD.pdf','2112.04426-RETRO.pdf','2310.11511-Self-RAG.pdf']),
 ('B-02','分别查找两个独立目标：（一）以双编码器进行开放域问答的段落检索，主要贡献和评测围绕检索器本身的研究；（二）通过学习特定反思标记，按需检索并自我评价生成的研究。符合任一目标即可，不要求一篇同时满足两个目标；尽量找全。', ['2004.04906-DPR.pdf','2310.11511-Self-RAG.pdf']),
 ('B-03','查找使用外部检索证据支持文本生成的原创方法论文。排除只提出段落检索器而没有生成模块的研究。正文提到检索器名称或使用它作基线，不是排除理由。不限定篇数。', ['2005.11401-RAG.pdf','2007.01282-FiD.pdf','2112.04426-RETRO.pdf','2310.11511-Self-RAG.pdf']),
]

HOLDOUT=[
 ('B-01','查找主要贡献是训练双编码器段落检索器、并在开放域问答中评估检索效果的论文。只把已有检索器接入生成系统的工作不符合这个目标。不限篇数。',['2004.04906-DPR.pdf']),
 ('B-02','分别寻找两种机制：（一）对多个检索段落分别编码，然后在同一个解码器中联合利用这些证据；（二）使用冻结的BERT近邻检索，并用分块交叉注意力增强语言建模。符合任意一种机制即可，不需同时满足，也不限篇数。',['2007.01282-FiD.pdf','2112.04426-RETRO.pdf']),
 ('B-03','查找研究量子计算中纠错门电路的原创实验方法论文；必须实际评估量子纠错电路的门操作误差。不限篇数。',[]),
]
FRESH=[
 ('B-01','分别检索两类独立研究：一类以冻结的BERT检索器和外部邻居语料增强语言建模；另一类学习反思标记以决定何时检索并评估生成支持程度。符合任意一类即可，各类内部条件须同时满足。不限篇数。',['2112.04426-RETRO.pdf','2310.11511-Self-RAG.pdf']),
 ('B-02','找这样的问答方法论文：每个检索段落连同问题独立编码，再把所有段落的编码结果串接，由同一个解码器联合注意。排除对各段落独立生成后再择优或边缘化的方案，也排除按token块分段进行交叉注意力的机制。不限篇数。',['2007.01282-FiD.pdf']),
 ('B-03','查找外部检索数据库明确达到万亿token量级、并利用该数据库增强语言模型预测的研究。规模必须是外部检索语料，不是基础模型的预训练token数或模型参数量。不限篇数。',['2112.04426-RETRO.pdf']),
]
if '--fresh' in sys.argv:
    QUESTIONS=FRESH
    sys.argv.remove('--fresh')
if '--holdout' in sys.argv:

    QUESTIONS=HOLDOUT
    sys.argv.remove('--holdout')

def specimens():
    replacements={cid:dict(label=cid+'-b6-public-conditions',require_nonempty=bool(expected),
        payload=runner.question(cid,q,['本地论文文件夹内的 PDF'])) for cid,q,expected in QUESTIONS}
    return [replacements.get(item['payload']['case_id'],item) for item in ORIGINAL_SPECIMENS()]

if __name__=='__main__':
    run_id=sys.argv[sys.argv.index('--run-id')+1]
    path=runner.ROOT/'output'/(run_id+'-frozen-questions.json')
    data={'kind':'public-paper developer-authored validation, not official answers','cases':QUESTIONS,
          'pdf_sha256':{name:hashlib.sha256((runner.ROOT/'paper_library/pdfs'/name).read_bytes()).hexdigest() for name in runner.PDFS}}
    if path.exists():raise RuntimeError('Frozen test already exists; use a new run id')
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    runner.specimens=specimens
    runner.main()
