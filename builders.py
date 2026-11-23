import re

from psycopg2 import sql

from db import SCHEMA, DbError

I = sql.Identifier
L = sql.Literal

NAME_RE = re.compile(r"^[^\W\d]\w{0,62}$")
TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_ ]*(\(\s*\d+\s*(,\s*\d+\s*)?\))?(\[\])?$")

FK_ACTIONS = ["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"]
AGG_FUNCS = ["COUNT", "SUM", "AVG", "MIN", "MAX"]
COMPARE_OPS = ["=", "<>", "<", ">", "<=", ">="]
TEXT_OPS = ["LIKE", "ILIKE", "NOT LIKE", "~", "~*", "!~", "!~*", "SIMILAR TO"]
WHERE_OPS = COMPARE_OPS + TEXT_OPS + ["IN", "IS NULL", "IS NOT NULL"]
JOIN_TYPES = ["INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL JOIN", "CROSS JOIN"]
DIRECTIONS = ["ASC", "DESC"]

ALTER_OPS = [
    ("add_column", "Добавить столбец (ADD COLUMN)"),
    ("drop_column", "Удалить столбец (DROP COLUMN)"),
    ("rename_column", "Переименовать столбец (RENAME COLUMN)"),
    ("rename_table", "Переименовать таблицу (RENAME TO)"),
    ("alter_type", "Изменить тип данных (ALTER COLUMN TYPE)"),
    ("set_not_null", "Задать NOT NULL"),
    ("drop_not_null", "Убрать NOT NULL"),
    ("set_default", "Задать значение по умолчанию (SET DEFAULT)"),
    ("drop_default", "Убрать значение по умолчанию (DROP DEFAULT)"),
    ("add_check", "Добавить CHECK"),
    ("add_unique", "Добавить UNIQUE"),
    ("add_fk", "Добавить FOREIGN KEY"),
    ("drop_constraint", "Удалить ограничение (DROP CONSTRAINT)"),
]


# Проверка имени
def check_name(name, what):
    if not name or not NAME_RE.match(name):
        raise DbError(f"Недопустимое {what}: «{name or ''}». Используйте буквы, цифры и _, начиная с буквы.")
    return name


# Проверка типа данных
def check_type(typ):
    typ = (typ or "").strip()
    if not TYPE_RE.match(typ):
        raise DbError(f"Недопустимый тип данных: «{typ}». Пример: varchar(50), numeric(6,2), text[].")
    return typ


def need(value, what, strip=True):
    text = "" if value is None else str(value)
    if (text.strip() if strip else text) == "":
        raise DbError(f"Не заполнено поле «{what}».")
    return text.strip() if strip else text


# Построение ALTER TABLE
def build_alter(op, table, p):
    head = sql.SQL("ALTER TABLE {} ").format(I(SCHEMA, table))
    col = lambda: I(need(p.get("column"), "Столбец"))
    if op == "add_column":
        body = sql.SQL("ADD COLUMN {} {}").format(I(check_name(p.get("name"), "имя столбца")),
                                                  sql.SQL(check_type(p.get("type"))))
        if p.get("default"):
            body += sql.SQL(" DEFAULT {}").format(L(p["default"]))
        if p.get("not_null"):
            body += sql.SQL(" NOT NULL")
    elif op == "drop_column":
        body = sql.SQL("DROP COLUMN {}").format(col())
        if p.get("cascade"):
            body += sql.SQL(" CASCADE")
    elif op == "rename_column":
        body = sql.SQL("RENAME COLUMN {} TO {}").format(col(), I(check_name(p.get("name"), "новое имя столбца")))
    elif op == "rename_table":
        body = sql.SQL("RENAME TO {}").format(I(check_name(p.get("name"), "новое имя таблицы")))
    elif op == "alter_type":
        typ = sql.SQL(check_type(p.get("type")))
        body = sql.SQL("ALTER COLUMN {} TYPE {}").format(col(), typ)
        if p.get("using"):
            body += sql.SQL(" USING {}::{}").format(col(), typ)
    elif op == "set_not_null":
        body = sql.SQL("ALTER COLUMN {} SET NOT NULL").format(col())
    elif op == "drop_not_null":
        body = sql.SQL("ALTER COLUMN {} DROP NOT NULL").format(col())
    elif op == "set_default":
        body = sql.SQL("ALTER COLUMN {} SET DEFAULT {}").format(col(), L(need(p.get("default"), "Значение")))
    elif op == "drop_default":
        body = sql.SQL("ALTER COLUMN {} DROP DEFAULT").format(col())
    elif op == "add_check":
        expr = need(p.get("expr"), "Условие CHECK")
        if ";" in expr:
            raise DbError("Условие CHECK не должно содержать «;».")
        body = sql.SQL("ADD CONSTRAINT {} CHECK ({})").format(I(check_name(p.get("name"), "имя ограничения")),
                                                              sql.SQL(expr))
    elif op == "add_unique":
        cols = p.get("columns") or []
        if not cols:
            raise DbError("Выберите хотя бы один столбец для UNIQUE.")
        body = sql.SQL("ADD CONSTRAINT {} UNIQUE ({})").format(I(check_name(p.get("name"), "имя ограничения")),
                                                               sql.SQL(", ").join(map(I, cols)))
    elif op == "add_fk":
        on_delete, on_update = p.get("on_delete", "NO ACTION"), p.get("on_update", "NO ACTION")
        if on_delete not in FK_ACTIONS or on_update not in FK_ACTIONS:
            raise DbError("Недопустимое действие ON DELETE / ON UPDATE.")
        body = sql.SQL("ADD CONSTRAINT {} FOREIGN KEY ({}) REFERENCES {} ({}) ON DELETE {} ON UPDATE {}").format(
            I(check_name(p.get("name"), "имя ограничения")), col(),
            I(SCHEMA, need(p.get("ref_table"), "Таблица-родитель")), I(need(p.get("ref_column"), "Столбец-родитель")),
            sql.SQL(on_delete), sql.SQL(on_update))
    elif op == "drop_constraint":
        body = sql.SQL("DROP CONSTRAINT {}").format(I(need(p.get("constraint"), "Ограничение")))
        if p.get("cascade"):
            body += sql.SQL(" CASCADE")
    else:
        raise DbError(f"Неизвестная операция: {op}")
    return head + body


# Условие WHERE
def condition(column, op, value):
    if op not in WHERE_OPS:
        raise DbError(f"Недопустимый оператор: {op}")
    c = I(column)
    if op in ("IS NULL", "IS NOT NULL"):
        return sql.SQL("{} " + op).format(c)
    if op == "IN":
        items = [v.strip() for v in (value or "").split(",") if v.strip()]
        if not items:
            raise DbError(f"Для IN укажите значения через запятую (столбец «{column}»).")
        return sql.SQL("{}::text = ANY({})").format(c, L(items))
    if op in TEXT_OPS:
        return sql.SQL("{}::text " + op + " {}").format(c, L(value or ""))
    return sql.SQL("{} " + op + " {}").format(c, L(value))


# Агрегатное выражение
def aggregate(func, column):
    if func not in AGG_FUNCS:
        raise DbError(f"Недопустимая функция: {func}")
    if column == "*":
        if func != "COUNT":
            raise DbError(f"{func}(*) не поддерживается — выберите столбец.")
        return sql.SQL("COUNT(*)")
    return sql.SQL(func + "({})").format(I(column))


# Конструктор SELECT
def build_select(table, columns, aggregates=(), where=(), where_join="AND", group_by=(),
                 having=(), order_by=(), limit=0):
    items = [I(c) for c in columns]
    for func, column, alias in aggregates:
        alias = alias or f"{func.lower()}_{'all' if column == '*' else column}"
        items.append(sql.SQL("{} AS {}").format(aggregate(func, column), I(alias)))
    if not items:
        raise DbError("Выберите хотя бы один столбец или агрегатную функцию.")
    q = sql.SQL("SELECT {} FROM {}").format(sql.SQL(", ").join(items), I(SCHEMA, table))
    if where:
        joiner = sql.SQL(" AND " if where_join == "AND" else " OR ")
        q += sql.SQL(" WHERE ") + joiner.join(condition(*w) for w in where)
    if group_by:
        q += sql.SQL(" GROUP BY ") + sql.SQL(", ").join(map(I, group_by))
    if having:
        parts = []
        for func, column, op, value in having:
            if op not in COMPARE_OPS:
                raise DbError(f"Недопустимый оператор HAVING: {op}")
            parts.append(sql.SQL("{} " + op + " {}").format(aggregate(func, column), L(need(value, "Значение HAVING"))))
        q += sql.SQL(" HAVING ") + sql.SQL(" AND ").join(parts)
    if order_by:
        parts = []
        for field, direction in order_by:
            if direction not in DIRECTIONS:
                raise DbError(f"Недопустимое направление сортировки: {direction}")
            parts.append(sql.SQL("{} " + direction).format(I(need(field, "Поле ORDER BY"))))
        q += sql.SQL(" ORDER BY ") + sql.SQL(", ").join(parts)
    if limit:
        q += sql.SQL(" LIMIT {}").format(L(int(limit)))
    return q


# Мастер JOIN
def build_join(base, steps, columns, limit=1000):
    tables = [base] + [s[1] for s in steps]
    repeated = {t for t in tables if tables.count(t) > 1}
    items = []
    for n, table in enumerate(tables, 1):
        alias = f"t{n}"
        for c in columns[table]:
            label = f"{alias}.{c}" if table in repeated else f"{table}.{c}"
            items.append(sql.SQL("{}.{} AS {}").format(I(alias), I(c), I(label)))
    q = sql.SQL("SELECT {} FROM {} AS t1").format(sql.SQL(", ").join(items), I(SCHEMA, base))
    for n, (join_type, table, left, right) in enumerate(steps, 2):
        if join_type not in JOIN_TYPES:
            raise DbError(f"Недопустимый тип соединения: {join_type}")
        q += sql.SQL(" " + join_type + " {} AS {}").format(I(SCHEMA, table), I(f"t{n}"))
        if join_type != "CROSS JOIN":
            if not left or not right:
                raise DbError(f"Для соединения с «{table}» выберите поля связи.")
            q += sql.SQL(" ON {}.{} = {}.{}").format(I(left[0]), I(left[1]), I(f"t{n}"), I(right))
    return q + sql.SQL(" LIMIT {}").format(L(limit))


STRING_FUNCS = [
    ("UPPER", "UPPER — верхний регистр", ()),
    ("LOWER", "LOWER — нижний регистр", ()),
    ("INITCAP", "INITCAP — Каждое Слово С Заглавной", ()),
    ("LENGTH", "LENGTH — длина строки", ()),
    ("TRIM", "TRIM — убрать пробелы/символы по краям", ("Символы (необязательно)",)),
    ("LTRIM", "LTRIM — убрать слева", ("Символы (необязательно)",)),
    ("RTRIM", "RTRIM — убрать справа", ("Символы (необязательно)",)),
    ("SUBSTRING", "SUBSTRING — подстрока по позиции", ("С позиции", "Длина (необязательно)")),
    ("SUBSTRING_RE", "SUBSTRING — подстрока по регулярке", ("Регулярное выражение",)),
    ("POSITION", "POSITION — позиция подстроки", ("Подстрока",)),
    ("REPLACE", "REPLACE — замена", ("Что заменить", "На что")),
    ("LPAD", "LPAD — дополнить слева", ("Итоговая длина", "Символ заполнения")),
    ("RPAD", "RPAD — дополнить справа", ("Итоговая длина", "Символ заполнения")),
    ("CONCAT", "CONCAT — объединить", ("Разделитель", "Текст (если нет 2-го столбца)")),
    ("PIPES", "|| — объединить оператором", ("Разделитель", "Текст (если нет 2-го столбца)")),
]


# Выражение строковой функции
def string_expr(func, x, p1="", p2="", y=None):
    if func in ("UPPER", "LOWER", "INITCAP", "LENGTH"):
        return sql.SQL(func + "({})").format(x)
    if func == "TRIM":
        return sql.SQL("TRIM(BOTH {} FROM {})").format(L(p1), x) if p1 else sql.SQL("TRIM({})").format(x)
    if func in ("LTRIM", "RTRIM"):
        return sql.SQL(func + "({}, {})").format(x, L(p1)) if p1 else sql.SQL(func + "({})").format(x)
    if func == "SUBSTRING":
        start = L(need(p1, "С позиции"))
        if p2:
            return sql.SQL("SUBSTRING({} FROM {}::int FOR {}::int)").format(x, start, L(p2))
        return sql.SQL("SUBSTRING({} FROM {}::int)").format(x, start)
    if func == "SUBSTRING_RE":
        return sql.SQL("SUBSTRING({} FROM {}::text)").format(x, L(need(p1, "Регулярное выражение")))
    if func == "POSITION":
        return sql.SQL("POSITION({}::text IN {})").format(L(need(p1, "Подстрока", strip=False)), x)
    if func == "REPLACE":
        return sql.SQL("REPLACE({}, {}, {})").format(x, L(need(p1, "Что заменить", strip=False)), L(p2))
    if func in ("LPAD", "RPAD"):
        length = L(need(p1, "Итоговая длина"))
        if p2:
            return sql.SQL(func + "({}, {}::int, {})").format(x, length, L(p2))
        return sql.SQL(func + "({}, {}::int)").format(x, length)
    if func in ("CONCAT", "PIPES"):
        other = y if y is not None else L(p2)
        if func == "CONCAT":
            return sql.SQL("CONCAT({}, {}, {})").format(x, L(p1), other)
        return sql.SQL("{} || {} || {}").format(x, L(p1), other)
    raise DbError(f"Неизвестная функция: {func}")


def _source(table, column, literal):
    if table:
        return sql.SQL("{}::text").format(I(column)), sql.SQL(" FROM {} LIMIT 200").format(I(SCHEMA, table))
    return L(literal), sql.SQL("")


# Запрос строковой функции
def build_string(func, table=None, column=None, literal="", p1="", p2="", column2=None):
    x, tail = _source(table, column, literal)
    y = sql.SQL("{}::text").format(I(column2)) if (table and column2) else None
    expr = string_expr(func, x, p1, p2, y)
    return sql.SQL("SELECT {x} AS {a}, {e} AS {b}, LENGTH({x}) AS {c}, LENGTH(({e})::text) AS {d}").format(
        x=x, e=expr, a=I("Исходное"), b=I("Результат"), c=I("Длина до"), d=I("Длина после")) + tail


# Все строковые функции сразу
def build_all_strings(table=None, column=None, literal=""):
    x, tail = _source(table, column, literal)
    demo = [
        ("UPPER", string_expr("UPPER", x)),
        ("LOWER", string_expr("LOWER", x)),
        ("INITCAP", string_expr("INITCAP", x)),
        ("LENGTH", string_expr("LENGTH", x)),
        ("TRIM", string_expr("TRIM", x)),
        ("SUBSTRING(1,5)", string_expr("SUBSTRING", x, "1", "5")),
        ("POSITION(' ')", string_expr("POSITION", x, " ")),
        ("REPLACE(' ','_')", string_expr("REPLACE", x, " ", "_")),
        ("LPAD(25,'*')", string_expr("LPAD", x, "25", "*")),
        ("RPAD(25,'.')", string_expr("RPAD", x, "25", ".")),
        ("CONCAT('[',x,']')", sql.SQL("CONCAT('[', {}, ']')").format(x)),
        ("x || ' #DDoS'", sql.SQL("{} || ' #DDoS'").format(x)),
    ]
    items = [sql.SQL("{} AS {}").format(x, I("Исходное"))]
    items += [sql.SQL("{} AS {}").format(e, I(name)) for name, e in demo]
    return sql.SQL("SELECT ") + sql.SQL(", ").join(items) + tail
