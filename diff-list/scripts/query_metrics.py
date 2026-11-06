#!/usr/bin/env python3
"""记录 Agent 查询的低成本遥测；不保存查询原文或源码证据。"""
import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path

LOG_NAME='query-metrics.jsonl'
QUERY_METRICS_VERSION=1


def metrics_path(analysis_path,review_path,run_schema_version):
    """仅识别 run_delivery 创建的固定运行目录，独立查询不记录。"""
    analysis=Path(analysis_path).resolve()
    review=Path(review_path).resolve()
    work=analysis.parent.parent
    if (analysis.name!='analysis.json' or analysis.parent.name!='analysis'
            or review!=work/'review.json'):
        return None
    state_path=work/'run-state.json'
    if not state_path.is_file():
        return None
    state=json.loads(state_path.read_text(encoding='utf-8-sig'))
    if (state.get('run_schema_version')!=run_schema_version
            or state.get('run_dir')!=str(work.parent.resolve())):
        return None
    return work/LOG_NAME


def request_hash(args,analysis_id):
    """相同查询、视图、分页和输出方式才属于重复查询。"""
    request={
        'analysis_id':analysis_id,
        'mode':query_mode(args),
        'query':args.query,
        'unit':args.unit,
        'fact':args.fact,
        'evidence':args.evidence,
        'view':args.view,
        'cursor':args.cursor,
        'limit':args.limit,
        'source_lines':args.source_lines,
        'output_to_file':bool(args.output),
    }
    return hashlib.sha256(json.dumps(
        request,ensure_ascii=False,sort_keys=True,separators=(',',':')
    ).encode('utf-8')).hexdigest()


def query_mode(args):
    for name in ('overview','audit','query','unit','fact','evidence'):
        if getattr(args,name):
            return name
    return 'unknown'


def record_query(path,args,analysis_id,status,returned_bytes,stdout_bytes,returned_items):
    if path is None:
        return
    event=dict(
        version=QUERY_METRICS_VERSION,
        timestamp=time.time(),
        request_hash=request_hash(args,analysis_id),
        mode=query_mode(args),
        view=args.view,
        status=status,
        returned_bytes=returned_bytes,
        stdout_bytes=stdout_bytes,
        returned_items=returned_items)
    payload=(json.dumps(event,ensure_ascii=False,sort_keys=True)+'\n').encode('utf-8')
    # 单次追加单行；不读改 run-state，避免与 Agent 计时写入互相覆盖。
    fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    try:
        length=os.write(fd,payload)
        if length!=len(payload):
            raise OSError('query metrics append is incomplete')
    finally:
        os.close(fd)


def summarize_queries(work_dir):
    path=Path(work_dir)/LOG_NAME
    summary=dict(
        calls=0,successes=0,failures=0,
        distinct_requests=0,repeated_calls=0,
        returned_items=0,returned_bytes=0,stdout_bytes=0,
        by_mode={},by_view={},invalid_events=0,
        note='returned_bytes 是查询生成 JSON 的 UTF-8 字节数，不等于 Agent 实际读取 Token；stdout_bytes 是直接终端输出字节数。')
    if not path.is_file():
        return summary
    modes,views,identities=Counter(),Counter(),Counter()
    with path.open('r',encoding='utf-8-sig') as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                row=json.loads(line)
                if (row.get('version')!=QUERY_METRICS_VERSION
                        or not isinstance(row.get('request_hash'),str)
                        or len(row['request_hash'])!=64
                        or row.get('status') not in ('ok','error')
                        or any(type(row.get(name))!=int or row[name]<0
                               for name in ('returned_bytes','stdout_bytes','returned_items'))):
                    raise ValueError('invalid metrics event')
            except (ValueError,TypeError,AttributeError):
                summary['invalid_events']+=1
                continue
            summary['calls']+=1
            summary['successes']+=int(row['status']=='ok')
            summary['failures']+=int(row['status']=='error')
            summary['returned_bytes']+=row['returned_bytes']
            summary['stdout_bytes']+=row['stdout_bytes']
            summary['returned_items']+=row['returned_items']
            modes[row.get('mode') or 'unknown']+=1
            views[row.get('view') or 'unknown']+=1
            identities[row['request_hash']]+=1
    summary['distinct_requests']=len(identities)
    summary['repeated_calls']=sum(count-1 for count in identities.values())
    summary['by_mode']=dict(sorted(modes.items()))
    summary['by_view']=dict(sorted(views.items()))
    return summary
