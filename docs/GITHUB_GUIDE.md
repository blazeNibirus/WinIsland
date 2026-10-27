# Как выложить WinIsland на GitHub (простыми словами)

**GitHub** — это сайт, где хранят проекты с историей изменений. Как Google Диск, только для кода:
любой может скачать, а вы видите, что и когда менялось.

Три слова, которые нужно знать:
- **Репозиторий** — папка проекта на GitHub.
- **Коммит** — «сохранение» с подписью, что именно вы изменили.
- **Ветка `main`** — главная, всегда рабочая версия проекта.

---

## Шаг 1. Подготовка (один раз)
1. Зарегистрируйтесь на https://github.com
2. Установите Git: https://git-scm.com/download/win (везде жмите «Next»).

## Шаг 2. Создайте пустой репозиторий
1. На GitHub: **+ → New repository**.
2. **Repository name:** `WinIsland`
3. **Description (карточка):** `Dynamic Island для Windows: плеер, уведомления, загрузки, погода, быстрые настройки`
4. Выберите **Public**.
5. **Ничего не отмечайте** (никаких README, .gitignore, license — они уже есть в проекте).
6. Нажмите **Create repository** и скопируйте ссылку вида `https://github.com/ВАШ_НИК/WinIsland.git`.

## Шаг 3. Загрузите проект — самый простой способ
1. Распакуйте архив проекта в папку.
2. Запустите **`git_publish.bat`** (двойной клик).
3. Вставьте ссылку из шага 2. При первом разе GitHub попросит войти в аккаунт в браузере — войдите.

Готово: файлы на GitHub, ветка `main` создана.

<details><summary>То же самое вручную (если интересно)</summary>

Откройте PowerShell в папке проекта:
```
git init
git branch -M main
git config commit.template .gitmessage
git add .
git commit -m "feat: первая версия WinIsland"
git remote add origin https://github.com/ВАШ_НИК/WinIsland.git
git push -u origin main
```
</details>

## Шаг 4. Оформите «карточку» проекта
На странице репозитория справа нажмите шестерёнку возле **About**:
- **Description** — одна строка о проекте (как в шаге 2).
- **Topics** (теги) — `windows`, `dynamic-island`, `pyqt6`, `python`, `desktop-widget`, `notifications`.
- Включите галочки **Releases**, **Packages** не нужны.

Картинка при отправке ссылки в соцсетях: **Settings → General → Social preview → Upload** (нужна PNG 1280×640; сделайте скриншот острова и вставьте).
В `README.md` потом замените баннер на реальный скриншот или GIF — так проект выглядит намного живее.

## Шаг 5. Как работать дальше (коммиты и ветка main)
Правило простое: **в `main` лежит только рабочее**, новое делаем в отдельной ветке.

```
git switch -c feat/новая-функция     # создали ветку
# ...меняете файлы...
git add .
git commit                           # откроется шаблон — заполните первую строку
git push -u origin feat/новая-функция
```
Потом на GitHub появится кнопка **Compare & pull request** → создаёте Pull Request → проверка (зелёная галочка) → **Merge**.

**Как писать коммиты** (шаблон подключён автоматически):
`feat: добавил чёлку` · `fix: остров не закрывался` · `docs: поправил инструкцию`

## Шаг 6. Защитите ветку main (настройка)
**Settings → Branches → Add branch ruleset** (или *Add rule*):
1. Name: `protect-main`, Enforcement: **Active**.
2. Target branches → **Include default branch** (это `main`).
3. Отметьте: **Restrict deletions**, **Block force pushes**, **Require a pull request before merging**,
   **Require status checks to pass** → выберите проверку `check` (из `ci.yml`).
4. **Create**.

Если вы работаете один и это мешает — оставьте только «Restrict deletions» и «Block force pushes».

## Шаг 7. Выпуск версии — чтобы скачивали готовый .exe
```
git tag v2.0.0
git push origin v2.0.0
```
GitHub сам соберёт `WinIsland.exe` (файл `.github/workflows/release.yml`) и выложит в разделе **Releases**.
Сборка идёт ~3–5 минут: смотрите вкладку **Actions**. Если она покраснела — откройте лог и пришлите мне, поправим.
> Сборка exe — самая капризная часть (программа использует системные библиотеки Windows). Установка из исходников через `install.bat` работает независимо от неё.

## Частые вопросы
- **Что значит «зелёная галочка»?** Автопроверка (`ci.yml`) прошла.
- **Я что-то сломал в коде.** Всё сохранено в истории: на GitHub откройте файл → **History**.
- **Как обновить у себя то, что поменял на сайте?** `git pull`.
