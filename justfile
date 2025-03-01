
setup:
    uv sync

lint:
    isort src/
    ruff format src/

tasks:
    rg --pretty --max-depth 50 --glob '!justfile' 'FIXME|TODO'

overview:
    eza --hyperlink --tree --long --group-directories-first --ignore-glob __pycache__ --ignore-glob node_modules --git-ignore
