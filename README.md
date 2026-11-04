# SIPL Language Toolkit

[![CI](https://github.com/Scooby-Doooo/TP/actions/workflows/ci.yml/badge.svg)](https://github.com/Scooby-Doooo/TP/actions/workflows/ci.yml)

Курсовий проєкт з теорії програмування: парсер, інтерпретатор, CLI та дебагер для мови SIPL.

## Обрані завдання

Трек «Interpreter + Debugger», заявлено 10 балів.

| ID | Завдання | Бали | Файли |
| -- | -------- | ---- | ----- |
| CORE-01 | Формальна специфікація | 0,5 | `sipl/src/sipl/grammar/sipl.lark` |
| CORE-02 | Парсер і AST | 1,5 | `sipl/src/sipl/parser.py`, `ast.py` |
| CORE-03 | Інтерпретатор | 2 | `sipl/src/sipl/interpreter/engine.py` |
| CORE-04 | Події та trace | 1 | `sipl/src/sipl/interpreter/events.py` |
| CORE-05 | CLI і JSON | 1 | `sipl/src/sipl/cli.py` |
| IDE-03 | Debug Adapter Protocol | 2,5 | `sipl/src/sipl/dap.py`, `vscode-sipl/` |
| REL-01 | CI | 0,5 | `.github/workflows/ci.yml` |
| REL-02 | Releases, SemVer, CHANGELOG | 0,5 | `.github/workflows/release.yml`, `CHANGELOG.md` |
| REL-03 | Публікація в PyPI | 0,5 | `.github/workflows/release.yml` |

## Встановлення і запуск

Потрібен Python 3.10 або новіший.

```bash
cd sipl
python -m venv .venv
.venv\Scripts\activate        # Linux, macOS: source .venv/bin/activate
pip install -e ".[dev]"
```

```bash
sipl check examples/power.sipl
sipl run examples/power.sipl --input "X=2 Y=10"
sipl trace examples/power.sipl --input "X=2 Y=1"
sipl parse examples/gcd.sipl --json
```

Друга команда виводить кінцевий стан:

```text
R = 1024
X = 2
Y = 0
```

Приклади лежать в `sipl/examples/`, програми з помилками в `sipl/examples/invalid/`.

## Як це працює

```text
текст -> parser.py (Lark) -> AST -> interpreter.execute() -> події -> CLI або dap.py -> VS Code
```

Парсер перетворює дерево Lark на власні вузли AST, і в кожного вузла є `SourceSpan`. Сам Lark за межі `parser.py` не виходить.

Інтерпретатор написаний як генератор подій. `run` вичитує його до кінця, `trace` виводить кожну подію, а дебагер просто перестає просити наступну, коли треба зупинитися. Тому код виконання існує в одному місці.

Адаптер DAP працює окремим процесом і лише транслює протокол. Розширення VS Code реєструє файли `.sipl` і запускає цей адаптер, логіки мови в ньому немає.

## Мова

```text
P ::= begin S end
S ::= x := a | S1 ; S2 | if b then S1 else S2 | while b do S | begin S end | skip
a ::= n | x | a1 + a2 | a1 - a2 | a1 * a2 | (a)
b ::= a1 = a2 | a1 > a2 | b1 ∨ b2 | ¬ b | (b)
```

Лексика:

- ключові слова: `begin end if then else while do skip or not`, регістр важливий;
- змінна: `[A-Za-z_][A-Za-z0-9_]*`, крім ключових слів;
- число: `-?[0-9]+`;
- пробіли й переводи рядків значення не мають, коментарів у мові немає.

Сама БНФ неоднозначна, тому в граматиці зафіксовано такі рішення:

- `*` має вищий пріоритет за `+` і `-`, усі три ліво-асоціативні;
- порівняння сильніше за `¬`, а `¬` сильніше за `∨`;
- `while` та `if` сильніші за `;`, тому кілька операторів у тілі циклу треба брати в `begin ... end`;
- `X-1` і `X -1` є відніманням, а `X := -1` присвоює від'ємне число. Для цього парсер використовує контекстний лексер. Унарного мінуса перед виразом немає, тому `-X` є помилкою;
- `or` і `not` є синонімами `∨` і `¬`, щоб програму можна було набрати зі звичайної клавіатури.

Під час виконання:

- читання змінної без значення є помилкою;
- обидві частини `∨` обчислюються завжди;
- кроком вважається присвоювання, `skip` або перевірка умови, типовий ліміт становить 100 000 кроків;
- результат арифметичної операції обмежено 10 000 біт, інакше `X := X * X` у циклі з'їв би всю пам'ять раніше, ніж спрацював би ліміт кроків.

## CLI

```text
sipl parse FILE [--json]
sipl check FILE [--json]
sipl run   FILE [--input "X=2 Y=10"] [--max-steps N] [--json]
sipl trace FILE [--input "X=2 Y=10"] [--max-steps N] [--json]
```

Без `--json` помилка йде у stderr:

```text
examples/invalid/trailing_semicolon.sipl:3:1: помилка[syntax]: Неочікуваний 'end'; очікувалось: 'begin', 'if', 'skip', 'while', 'змінна'
```

Коди завершення: `0` успіх, `1` синтаксична помилка, `2` неправильний виклик або файл не читається, `3` помилка виконання.

### Формат JSON

Кожна відповідь містить `schema_version` (зараз 1), `command`, `ok` і `diagnostics`. Решта полів залежить від команди:

| Команда | Поля |
| ------- | ---- |
| `parse` | `ast` |
| `check` | немає |
| `run` | `state`, `steps` |
| `trace` | `events`, `state`, `steps` |

Якщо сталася помилка виконання, `state` містить останній стан, `events` містить події до помилки, а `steps` дорівнює `null`. Якщо виконання не почалося, `state` і `ast` теж дорівнюють `null`. Помилки самих аргументів виводить argparse звичайним текстом навіть із `--json`.

Елемент `diagnostics` має `code`, `message` і `span`. Рядки та колонки в `span` рахуються від 1, `end_column` у діапазон не входить:

```json
{ "start_line": 3, "start_column": 1, "end_line": 3, "end_column": 4 }
```

Коди помилок: `syntax`, `number-too-long`, `undefined-variable`, `value-too-large`, `step-limit`, `too-deep`, `cannot-read-file`, `invalid-input`.

Вузли AST (кожен ще має `type` і `span`):

```text
Program  body                Number   value
Assign   name, value         Var      name
Seq      statements          BinOp    op, left, right
If       condition,          Compare  op, left, right
         then_branch,        Or       left, right
         else_branch         Not      operand
While    condition, body
Block    body
Skip
```

Події trace (кожна ще має `event` і `span`):

```text
BeforeStatement   statement             перед оператором
AfterStatement    statement             після оператора
VariableChanged   name, old, new        після присвоювання, old = null для нової змінної
ConditionChecked  value                 після перевірки умови, span вказує на умову
Finished          state, steps          програма завершилась
```

`statement` набуває значень `assign`, `if`, `while`, `block`, `skip`. Послідовність `S1; S2` окремих подій не дає. З Python ті самі події повертає `sipl.interpreter.execute(program, state)`.

## Налагодження у VS Code

Для збірки розширення потрібен Node.js:

```bash
cd vscode-sipl
npx --yes @vscode/vsce@3 package --no-dependencies --allow-missing-repository --skip-license
code --install-extension vscode-sipl-0.1.0.vsix
```

У налаштуваннях VS Code треба вказати `sipl.pythonPath`: абсолютний шлях до Python, де встановлено `sipl`, наприклад `C:\path\to\TP\sipl\.venv\Scripts\python.exe`.

Далі відкриваємо файл `.sipl`, ставимо breakpoint, натискаємо F5 і вводимо початковий стан, наприклад `X=2 Y=10`. Працюють breakpoints, continue, step, панель змінних, Watch, Debug Console і підказки при наведенні.

Цикл зупиняється на умові `while` на кожній ітерації. Процедур у SIPL немає, тому Step Over, Step Into і Step Out роблять одне й те саме: переходять до наступного оператора або перевірки умови. Pause нічого не робить, бо адаптер обробляє запити між запусками, а ліміт кроків гарантує, що програма зупиниться сама.

Щоб не вводити стан щоразу, можна додати `launch.json` з полями `"type": "sipl"`, `"request": "launch"`, `"name"`, `"program": "${file}"`, `"input": "X=2 Y=10"`. Необов'язкові поля: `maxSteps`, `stopOnEntry`.

## Тести

З папки `sipl`:

```bash
pytest --cov --cov-fail-under=80
ruff check .
mypy
```

Тести покривають граматику (позитивні й негативні випадки), діагностику, виконання, trace, контракт CLI та протокол DAP, зокрема адаптер як окремий процес.

## Реліз

1. Оновити версію в `sipl/src/sipl/__init__.py` і `vscode-sipl/package.json`, додати розділ у `CHANGELOG.md`.
2. Створити й запушити тег `vX.Y.Z`.

`release.yml` звіряє версії з тегом, публікує пакет у PyPI й створює GitHub Release з артефактами. Для PyPI потрібно один раз налаштувати Trusted Publisher для цього репозиторію, workflow `release.yml` і середовища `pypi`.

## Обмеження

- Парсер повідомляє лише про першу синтаксичну помилку у файлі.
- Операцій `div` і `mod` у мові немає.
- Граматика лежить у `sipl/src/sipl/grammar/`, бо парсер читає її під час виконання, тому вона має потрапляти в пакет.
