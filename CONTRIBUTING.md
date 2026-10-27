# Как вносить изменения

1. Ветка `main` — всегда рабочая версия. Напрямую в неё не пишем.
2. Для каждой задачи — своя ветка: `feat/notch-style`, `fix/close-delay`, `docs/readme`.
3. Коммиты по шаблону (подключается командой `git config commit.template .gitmessage`):
   `feat: …`, `fix: …`, `perf: …`, `docs: …`, `chore: …`.
4. Когда готово — Pull Request в `main`. Должна пройти проверка (зелёная галочка).
5. Новая версия — метка `vX.Y.Z`: GitHub сам соберёт `WinIsland.exe` и выложит в «Releases».

Подробная инструкция для новичков — в файле `docs/GITHUB_GUIDE.md`.
