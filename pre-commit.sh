#!/usr/bin/env bash
# pre-commit script which runs mypy and (optionally) tests

EXIT_CODE=0
TOP_LEVEL=$(git rev-parse --show-toplevel)
pushd "$TOP_LEVEL" 2>&1 1>/dev/null

# This three-command dance stashes unstaged changes + untracked files.
# This way, mypy runs against the state of the repo as it is going to be committed.
# --no-verify inhibits the pre-commit hook so we don't loop infinitely
git commit --no-verify -m 'Save index' 2>&1 1>/dev/null
if [ $? -eq 1 ]
then
    echo "No staged changes."
    popd 2>&1 1>/dev/null
    exit 1
fi
git stash push -u -m 'Unstaged changes and untracked files' 2>&1 1>/dev/null
git reset --soft HEAD^ 2>&1 1>/dev/null

mypy bridge

if [ $? -eq 1 ]
then
	echo "Error during mypy execution, aborting commit..."
	EXIT_CODE=1
fi

git stash pop 2>&1 1>/dev/null
popd 2>&1 1>/dev/null

exit $EXIT_CODE
