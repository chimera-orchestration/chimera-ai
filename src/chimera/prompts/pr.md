Write the pull request title and body for branch `$GOAL` of $PROJECT, targeting `$BASE`.

Output the title on the first line — max 60 characters, imperative mood, why not what,
no trailing period, no type prefix — then a blank line, then the body.

The body is a succinct summary of WHY this change is going up: a short paragraph or two
of plain prose. Reviewers can read the diff and the commit list for themselves, so never
restate either. Link anything the commits or the branch name reference — issue and
ticket ids, URLs, threads — and say what the code can't: motivation, trade-offs taken,
what to look at first. Never claim anything the commits below don't support.

The branch's commits, oldest first:

$COMMITS

Output only the title and body — no preamble, no code fences.
