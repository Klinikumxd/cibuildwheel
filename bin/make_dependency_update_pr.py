#!/usr/bin/env python3

from __future__ import annotations

import os
import sys
import textwrap
import time
from pathlib import Path
from subprocess import run

import click


def shell(cmd, **kwargs):
    return run([cmd], shell=True, **kwargs)


def git_repo_has_changes():
    unstaged_changes = shell('git diff-index --quiet HEAD --').returncode != 0
    staged_changes = shell('git diff-index --quiet --cached HEAD --').returncode != 0
    return unstaged_changes or staged_changes


@click.command()
def main():
    project_root = Path(__file__).parent / '..'
    os.chdir(project_root)

    if git_repo_has_changes():
        print('Your git repo has uncommitted changes. Commit or stash before continuing.')
        sys.exit(1)

    previous_branch = shell(
        'git rev-parse --abbrev-ref HEAD', check=True, capture_output=True, encoding='utf8'
    ).stdout.strip()

    shell('git fetch origin', check=True)

    timestamp = time.strftime('%Y-%m-%dT%H-%M-%S', time.gmtime())
    branch_name = f'update-constraints-{timestamp}'

    shell(f'git checkout -b {branch_name} origin/master', check=True)

    try:
        shell('bin/update_dependencies.py', check=True)

        if not git_repo_has_changes():
            print('Done: no constraint updates required.')
            return

        shell('git commit -a -m "Update dependencies"', check=True)
        body = textwrap.dedent(
            f'''
                    Update the versions of our dependencies.

                    PR generated by `{os.path.basename(__file__)}`.
                '''
        )
        run(
            [
                'gh',
                'pr',
                'create',
                '--repo=joerick/cibuildwheel',
                '--base=master',
                "--title=Update dependencies",
                f"--body='{body}'",
            ],
            check=True,
        )

        print('Done.')
    finally:
        # remove any local changes
        shell('git checkout -- .')
        shell(f'git checkout {previous_branch}', check=True)
        shell(f'git branch -D --force {branch_name}', check=True)


if __name__ == '__main__':
    main.main(standalone_mode=True)
