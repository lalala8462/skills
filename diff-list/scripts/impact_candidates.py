#!/usr/bin/env python3
"""Search a specific Git revision for potential callers/references; never assert binding."""
import argparse
import subprocess
import sys


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',required=True)
    p.add_argument('--rev',required=True,help='Explicit target revision (not implicit HEAD)')
    p.add_argument('--symbol',action='append',required=True,help='Repeat for each symbol')
    p.add_argument('--limit',type=int,default=80)
    args = p.parse_args(argv)
    if args.limit < 1:
        p.error('--limit must be positive')
    base = ['git','-C',args.repo,'-c','core.quotePath=false',
            '-c','i18n.logOutputEncoding=utf-8']
    try:
        check = subprocess.run(base+['rev-parse','--verify',args.rev+'^{commit}'],
            capture_output=True,text=True,encoding='utf-8',errors='replace')
    except OSError as exc:
        print('ERROR: Git executable or repository unavailable: '+str(exc),file=sys.stderr)
        return 2
    if check.returncode:
        print('ERROR: invalid revision: '+check.stderr.strip(),file=sys.stderr)
        return 2
    sha = check.stdout.strip()
    print('Revision: '+sha+'\nCandidate references only; inspect types, routes and actual bindings.')
    for sym in args.symbol:
        if not sym.strip():
            continue
        run = subprocess.run(base+['grep','-n','-F','-e',sym,sha,'--'],
            capture_output=True,text=True,encoding='utf-8',errors='replace')
        if run.returncode not in (0,1):
            print('ERROR: '+run.stderr.strip(),file=sys.stderr)
            return 2
        lines = run.stdout.splitlines()
        print('\nSymbol: '+sym+' | matches: '+str(len(lines)))
        print('\n'.join(lines[:args.limit]))
        if len(lines)>args.limit:
            print('... '+str(len(lines)-args.limit)+' more; raise --limit to inspect')
    return 0


if __name__=='__main__':
    sys.exit(main())
