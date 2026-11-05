#!/usr/bin/env python3
"""Create an explicitly unfinished report from the heading-only template (Python 3.8+)."""
import argparse
import datetime
import re
import sys
from pathlib import Path


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--template',default=str(Path(__file__).resolve().parents[1]/'assets'/'diff-list-template.md'))
    p.add_argument('--outdir',default=r'G:\tmp')
    p.add_argument('--filename',default=None)
    a=p.parse_args(argv)
    try:
        template=Path(a.template).read_text(encoding='utf-8-sig')
        if not template.strip() or any(line.strip() and not re.match(r'^#{1,6} ',line)
           for line in template.splitlines()):
            raise ValueError('Template must contain Markdown headings and blank lines only')
        filename=a.filename or ('diff-list-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.md')
        if Path(filename).name != filename or not filename.lower().endswith('.md'):
            raise ValueError('filename must be a .md basename')
        outdir=Path(a.outdir)
        outdir.mkdir(parents=True,exist_ok=True)
        target=outdir/filename
        lines=template.rstrip().splitlines()
        lines.insert(1, '\n<!-- diff-list-status: draft -->\n'
                        '**报告状态：未完成。** 以下内容随分析逐步补充；'
                        '尚未完成覆盖校验和测试用例导出。\n')
        with target.open('x',encoding='utf-8',newline='\n') as f:
            f.write('\n'.join(lines)+'\n')
        print(str(target.resolve()))
        return 0
    except (OSError,ValueError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
