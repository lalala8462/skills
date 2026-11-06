#!/usr/bin/env python3
"""统一编排 diff-list 交付流程，并记录各阶段真实耗时。"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR=Path(__file__).resolve().parent
STATE_NAME='run-state.json'


def _iso(timestamp=None):
    value=datetime.fromtimestamp(timestamp if timestamp is not None else time.time(),timezone.utc)
    return value.isoformat(timespec='milliseconds').replace('+00:00','Z')


def _write_state(path,state):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(str(temporary),str(path))


def _read_state(run_dir):
    run_dir=Path(run_dir).resolve()
    state_path=run_dir/'.work'/STATE_NAME
    if not state_path.is_file():
        raise ValueError('run 目录缺少 .work/{}'.format(STATE_NAME))
    state=json.loads(state_path.read_text(encoding='utf-8-sig'))
    if state.get('run_dir')!=str(run_dir):
        raise ValueError('run-state.json 与当前 run 目录不匹配')
    return run_dir,state_path,state


def _cleanup(paths):
    for value in paths:
        path=Path(value)
        if path.is_file():
            path.unlink()


def _record_stage_attempt(state,name,started,ended,status,returncode):
    attempt=dict(
        started_at=_iso(started),
        ended_at=_iso(ended),
        elapsed_seconds=round(ended-started,3),
        status=status,
        returncode=returncode)
    previous=(state.get('stages') or {}).get(name)
    attempts=[]
    if previous:
        attempts=list(previous.get('attempts') or [])
        if not attempts:
            attempts.append(dict(
                started_at=previous.get('started_at'),
                ended_at=previous.get('ended_at'),
                elapsed_seconds=float(previous.get('elapsed_seconds',0)),
                status=previous.get('status'),
                returncode=previous.get('returncode',0)))
    attempts.append(attempt)
    state.setdefault('stages',{})[name]=dict(
        started_at=attempts[0]['started_at'],
        ended_at=attempt['ended_at'],
        elapsed_seconds=round(sum(float(row.get('elapsed_seconds',0)) for row in attempts),3),
        status=status,
        returncode=returncode,
        attempts=attempts)


def _run_stage(state_path,state,name,commands,cleanup=()):
    _cleanup(cleanup)
    started=time.time()
    status='completed'
    returncode=0
    try:
        for command in commands:
            result=subprocess.run(command)
            returncode=result.returncode
            if returncode:
                status='failed'
                break
    except OSError:
        status='failed'
        returncode=127
    ended=time.time()
    _record_stage_attempt(state,name,started,ended,status,returncode)
    if status!='completed':
        state['status']='failed'
        state['failed_stage']=name
    _write_state(state_path,state)
    if status!='completed':
        raise RuntimeError('{} 阶段失败，returncode={}'.format(name,returncode))


def _begin_agent_wait(state_path,state,status,reason):
    waiting=time.time()
    state['status']=status
    state['waiting_started_at']=_iso(waiting)
    state['waiting_started_epoch']=waiting
    state['waiting_reason']=reason
    _write_state(state_path,state)


def _record_agent_interval(state_path,state):
    started=state.get('waiting_started_epoch')
    if not isinstance(started,(int,float)):
        return
    ended=time.time()
    row=state.setdefault('stages',{}).get('agent_between_steps') or dict(
        started_at=_iso(started),elapsed_seconds=0.0,status='completed',returncode=0,segments=[])
    elapsed=max(0.0,ended-started)
    row['ended_at']=_iso(ended)
    row['elapsed_seconds']=round(float(row.get('elapsed_seconds',0.0))+elapsed,3)
    row['status']='completed'
    row['returncode']=0
    row.setdefault('segments',[]).append(dict(
        reason=state.get('waiting_reason') or 'agent_review',
        started_at=_iso(started),
        ended_at=_iso(ended),
        elapsed_seconds=round(elapsed,3)))
    state['stages']['agent_between_steps']=row
    state.pop('waiting_started_at',None)
    state.pop('waiting_started_epoch',None)
    state.pop('waiting_reason',None)
    _write_state(state_path,state)


def _print_timings(state):
    order=('analyze','review_workbench','agent_between_steps','preflight',
           'coverage','excel','report','finalize')
    for name in order:
        row=(state.get('stages') or {}).get(name)
        if not row:
            continue
        print('{:<20} {:>9.3f}s  {}'.format(name,float(row.get('elapsed_seconds',0)),row.get('status','')))


def _new_run_dir(root):
    root=Path(root).resolve()
    root.mkdir(parents=True,exist_ok=True)
    base=datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:19]
    candidate=root/base
    suffix=1
    while candidate.exists():
        candidate=root/(base+'-{:02d}'.format(suffix))
        suffix+=1
    candidate.mkdir()
    return candidate


def _prepare(args):
    run_dir=_new_run_dir(args.runs_root)
    work=run_dir/'.work'
    analysis_dir=work/'analysis'
    work.mkdir()
    state_path=work/STATE_NAME
    state=dict(
        run_schema_version=1,
        run_dir=str(run_dir),
        created_at=_iso(),
        status='preparing',
        source={},
        stages={})
    command=[sys.executable,'-B',str(SCRIPT_DIR/'analyze.py')]
    if args.manifest:
        if args.repo or args.rev or args.base or args.head:
            raise ValueError('--manifest 不能与 --repo/--rev/--base/--head 同时使用')
        manifest=str(Path(args.manifest).resolve())
        command.extend(['--manifest',manifest])
        state['source']={'manifest':manifest}
    else:
        if not args.repo:
            raise ValueError('首次运行必须提供 --repo 或 --manifest')
        command.extend(['--repo',args.repo])
        if args.rev and not (args.base or args.head):
            for revision in args.rev:
                command.extend(['--rev',revision])
            state['source']={'repo':args.repo,'revisions':list(args.rev)}
        elif not args.rev and args.base and args.head:
            command.extend(['--base',args.base,'--head',args.head])
            state['source']={'repo':args.repo,'range':{'base':args.base,'head':args.head}}
        else:
            raise ValueError('必须使用 --rev，或同时提供 --base/--head')
    if args.rules:
        rules=str(Path(args.rules).resolve())
        command.extend(['--rules',rules])
        state['source']['rules']=rules
    command.extend(['--outdir',str(analysis_dir)])
    _write_state(state_path,state)
    _run_stage(state_path,state,'analyze',[command])

    review=work/'review.json'
    cases=work/'test-cases.json'
    report_data=work/'report-data.json'
    review_command=[
        sys.executable,'-B',str(SCRIPT_DIR/'review_workbench.py'),
        '--analysis',str(analysis_dir/'analysis.json'),
        '--output',str(review),
        '--cases-template',str(cases),
        '--report-template',str(report_data)]
    _run_stage(state_path,state,'review_workbench',[review_command])
    state.pop('failed_stage',None)
    _begin_agent_wait(state_path,state,'waiting_agent','initial_business_review')
    print('RUN_DIR: '+str(run_dir))
    print('AGENT_INPUT: '+str(cases))
    print('AGENT_INPUT: '+str(report_data))
    print('REVIEW: '+str(review))
    print('RESUME: {} -B {} --resume {}'.format(sys.executable,SCRIPT_DIR/'run_delivery.py',run_dir))
    _print_timings(state)
    return 0


def _finalize_stage(state_path,state,run_dir,work,analysis,review,cases,report_data):
    coverage=work/'coverage.json'
    excel=work/'test-cases.xlsx'
    report=work/'report.md'
    started=time.time()
    status='completed'
    returncode=0
    commands=[
        [sys.executable,'-B',str(SCRIPT_DIR/'append_run_dirs.py'),str(report),'--source',str(analysis)],
        [sys.executable,'-B',str(SCRIPT_DIR/'validate_report.py'),str(report),
         '--analysis',str(analysis),'--review',str(review),'--cases',str(cases),'--finalize']]
    try:
        for command in commands:
            result=subprocess.run(command)
            returncode=result.returncode
            if returncode:
                status='failed'
                break
        if status=='completed':
            for source,name in ((coverage,'coverage.json'),(excel,'test-cases.xlsx'),(report,'report.md')):
                if not source.is_file():
                    raise OSError('缺少待发布文件: '+str(source))
                os.replace(str(source),str(run_dir/name))
    except OSError:
        status='failed'
        returncode=127
    ended=time.time()
    _record_stage_attempt(state,'finalize',started,ended,status,returncode)
    if status=='completed':
        state['status']='completed'
        state['completed_at']=_iso(ended)
        state.pop('failed_stage',None)
    else:
        state['status']='failed'
        state['failed_stage']='finalize'
    _write_state(state_path,state)
    if status!='completed':
        raise RuntimeError('finalize 阶段失败，returncode={}'.format(returncode))


def _resume(run_dir_value):
    run_dir,state_path,state=_read_state(run_dir_value)
    if state.get('status')=='completed':
        _print_timings(state)
        print('DELIVERY: '+str(run_dir))
        return 0
    if state.get('status') not in ('waiting_agent','waiting_coverage_review','failed'):
        raise ValueError('当前 run 状态不允许 resume: '+str(state.get('status')))
    _record_agent_interval(state_path,state)

    work=run_dir/'.work'
    analysis=work/'analysis'/'analysis.json'
    review=work/'review.json'
    cases=work/'test-cases.json'
    report_data=work/'report-data.json'
    coverage=work/'coverage.json'
    excel=work/'test-cases.xlsx'
    report=work/'report.md'

    preflight=[
        sys.executable,'-B',str(SCRIPT_DIR/'delivery_preflight.py'),
        '--analysis',str(analysis),'--review',str(review),
        '--cases',str(cases),'--report-data',str(report_data)]
    _run_stage(state_path,state,'preflight',[preflight])

    coverage_command=[
        sys.executable,'-B',str(SCRIPT_DIR/'validate_analysis.py'),
        '--analysis',str(analysis),'--review',str(review),'--cases',str(cases),
        '--coverage-output',str(coverage)]
    _run_stage(state_path,state,'coverage',[coverage_command],cleanup=(coverage,))
    coverage_data=json.loads(coverage.read_text(encoding='utf-8-sig'))
    pending=coverage_data.get('pending_review') or []
    if pending:
        state=json.loads(state_path.read_text(encoding='utf-8-sig'))
        _begin_agent_wait(state_path,state,'waiting_coverage_review','coverage_pending_review')
        print('COVERAGE_REVIEW: {} pending unit(s)'.format(len(pending)))
        print('COVERAGE: '+str(coverage))
        print('PENDING: '+','.join(row.get('unit_id','') for row in pending))
        print('说明：已有场景已隐含覆盖时，把对应 review_unit_id 加到该用例；否则补充新用例。')
        print('RESUME: {} -B {} --resume {}'.format(sys.executable,SCRIPT_DIR/'run_delivery.py',run_dir))
        state=json.loads(state_path.read_text(encoding='utf-8-sig'))
        _print_timings(state)
        return 0

    excel_command=[
        sys.executable,'-B',str(SCRIPT_DIR/'export_test_cases.py'),
        '--input',str(cases),'--output',str(excel),
        '--analysis',str(analysis),'--review',str(review)]
    _run_stage(state_path,state,'excel',[excel_command],cleanup=(excel,))

    report_command=[
        sys.executable,'-B',str(SCRIPT_DIR/'generate_report.py'),
        '--data',str(report_data),'--analysis',str(analysis),'--review',str(review),
        '--cases',str(cases),'--excel',str(excel),'--output',str(report)]
    _run_stage(state_path,state,'report',[report_command],cleanup=(report,))

    _finalize_stage(state_path,state,run_dir,work,analysis,review,cases,report_data)
    state=json.loads(state_path.read_text(encoding='utf-8-sig'))
    _print_timings(state)
    print('DELIVERY: '+str(run_dir))
    print('OUTPUT: '+str(run_dir/'test-cases.xlsx'))
    print('OUTPUT: '+str(run_dir/'report.md'))
    print('OUTPUT: '+str(run_dir/'coverage.json'))
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo')
    parser.add_argument('--rev',action='append')
    parser.add_argument('--base')
    parser.add_argument('--head')
    parser.add_argument('--manifest')
    parser.add_argument('--rules')
    parser.add_argument('--runs-root',default='runs')
    parser.add_argument('--resume',help='继续指定 run 目录，完成预检、coverage、Excel、report 和 finalize')
    args=parser.parse_args(argv)
    try:
        if args.resume:
            if any((args.repo,args.rev,args.base,args.head,args.manifest,args.rules)):
                raise ValueError('--resume 不能再提供分析范围参数')
            return _resume(args.resume)
        return _prepare(args)
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,IndexError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
