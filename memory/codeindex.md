# codeindex

## Поток решений

- Баг эмбеддингов кода: codeindex.py:155 idx = symbols + i удваивает индекс, хвост символов каждого файла теряет вектор (12 в semantic-memory, 5 в magpie); фикс idx = symbols + тест count(sym)==count(sym_vec) + --reindex, отдельный коммит
