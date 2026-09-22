#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Автономный тест ЛОГИКИ «Состояние задачи» (Task State Machine).

Проверяет КОНЕЧНЫЙ АВТОМАТ задачи на сценарии
«Разработка приложения на Qt + C++»:
    планирование -> выполнение (разработка/сборка) -> проверка ->
    (возврат при найденном баге) -> проверка -> завершение,
с паузами, продолжением и записью/чтением состояния из хранилища.

Тест НЕ требует сети/LLM: проверяется чистая логика автомата
(rtk_app.task_state.TaskState) и интеграция с хранилищем
(rtk_app.session_store.SessionStore). Запуск:
    python tests/check_task_state.py
"""
import os
import sys
import tempfile

# Корень проекта — на уровень выше tests/ (чтобы импортировать rtk_app и tests).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rtk_app.task_state import TaskState, STAGES, ALLOWED_TRANSITIONS
from rtk_app.session_store import SessionStore
from tests.harness import check, section, finish, ensure_utf8

GOAL = "Разработка приложения на Qt + C++"


def test_clean_machine():
    """Чистая логика автомата (без хранилища)."""
    section("1. Автомат: базовые переходы и запреты")
    t = TaskState()
    check("пустое состояние не активно", not t.active)

    t.start(GOAL, step="согласовать требования", expected="подтвердить ТЗ")
    check("старт: активно, этап planning",
          t.active and t.stage == "planning", t.stage)
    check("старт: цель сохранена", t.goal == GOAL, t.goal)

    ok, _ = t.advance("done")
    check("запрет прыжка planning->done", not ok)
    check("этап не изменился после запрета", t.stage == "planning", t.stage)

    ok, _ = t.advance("execution", step="разработка UI на QWidget",
                      expected="сборка проекта", note="ТЗ утверждено")
    check("planning->execution разрешён", ok)
    check("шаг обновлён", t.step == "разработка UI на QWidget", t.step)

    ok, _ = t.advance("validation", step="сборка qmake/CMake + unit-тесты",
                      expected="результат прогона тестов")
    check("execution->validation разрешён", ok)

    ok, _ = t.advance("execution", step="исправление бага в модели",
                      expected="повторная сборка", note="тест нашёл дефект")
    check("откат validation->execution разрешён", ok)

    ok, _ = t.advance("validation", step="повторная проверка",
                      expected="подтверждение готовности")
    check("повторный execution->validation разрешён", ok)

    ok, _ = t.advance("planning")
    check("нельзя вернуться к planning из validation", not ok)

    ok, _ = t.finish("приложение собрано и протестировано")
    check("finish из validation разрешён", ok)
    check("этап стал done", t.stage == "done", t.stage)

    ok, _ = t.advance("execution")
    check("из done переходы запрещены", not ok)


def test_pause_resume_keeps_position():
    """Пауза на любом этапе и продолжение без потери позиции."""
    section("2. Автомат: пауза/продолжение (без повторных объяснений)")
    t = TaskState()
    t.start(GOAL, step="проектирование архитектуры", expected="подтвердить схему")

    for stage, step, exp in [
        ("planning", "проектирование архитектуры", "подтвердить схему"),
        ("execution", "реализация протокола связи", "код-ревью"),
        ("validation", "нагрузочное тестирование", "отчёт о тестах"),
    ]:
        if stage != t.stage:
            t.advance(stage, step=step, expected=exp)
        ok, _ = t.pause("пауза: перерыв")
        check("пауза на этапе %s" % stage, ok and t.paused)
        ok, _ = t.resume()
        check("продолжение на этапе %s" % stage,
              ok and not t.paused and t.stage == stage and t.step == step,
              "%s / %s" % (t.stage, t.step))

    check("цель не потерялась после пауз", t.goal == GOAL, t.goal)
    kinds = [h.get("kind") for h in t.history]
    check("журнал содержит pause и resume",
          ("pause" in kinds) and ("resume" in kinds), kinds[-6:])


def test_prompt_block():
    """Состояние задачи попадает в системный промпт агента."""
    section("3. Промпт: состояние задачи передаётся модели")
    t = TaskState()
    t.start(GOAL, step="сборка окружения Qt", expected="установить Qt-комплект")
    t.advance("execution", step="написание MainWindow", expected="первый запуск")
    t.pause("ожидание SDK")
    block = t.system_prompt_block()
    check("блок не пуст", bool(block))
    check("в блоке есть цель", GOAL in block)
    check("в блоке есть этап execution", "execution" in block)
    check("в блоке есть шаг", "написание MainWindow" in block)
    check("в блоке указана пауза", "пауз" in block.lower())


def test_store_persistence():
    """Интеграция с хранилищем: сохранение/чтение состояния задачи."""
    section("4. Хранилище: сохранение и продолжение между запусками")
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    os.remove(tmp.name)
    try:
        s = SessionStore(tmp.name)
        check("в новом хранилище задачи нет",
              not s.get_task_state().get("active"))

        s.start_task(GOAL, step="планирование спринта", expected="оценка")
        s.advance_task("execution", step="разработка движка",
                       expected="сборка без ошибок", note="план принят")
        s.advance_task("validation", step="сборка + тесты",
                       expected="результат CI")
        s.pause_task("пауза до утра")
        check("пауза сохранена",
              s.get_task_state().get("paused") is True)

        # Имитируем ПЕРЕЗАПУСК: новый объект читает тот же файл.
        s2 = SessionStore(tmp.name)
        st = s2.get_task_state()
        check("этап сохранился после перезапуска",
              st.get("stage") == "validation", st.get("stage"))
        check("шаг сохранился", st.get("step") == "сборка + тесты", st.get("step"))
        check("пауза сохранилась", st.get("paused") is True)
        check("цель сохранилась", st.get("goal") == GOAL, st.get("goal"))

        # Продолжаем ПОСЛЕ перезапуска — без повторных объяснений.
        r = s2.resume_task()
        check("продолжение после перезапуска ок", r.get("ok"))
        check("позиция не потеряна",
              s2.get_task_state().get("stage") == "validation"
              and s2.get_task_state().get("step") == "сборка + тесты")

        # Завершение и проверка запрета finish не из validation.
        r = s2.advance_task("execution")   # возврат при баге
        check("возврат на execution ок", r.get("ok"))
        bad = s2.finish_task()
        check("finish не из validation запрещён", bad.get("ok") is False,
              bad.get("error"))
        s2.advance_task("validation")
        good = s2.finish_task("приложение собрано, тесты пройдены")
        check("finish из validation ок", good.get("ok"))
        check("этап done", s2.get_task_state().get("stage") == "done")

        # Новый разговор сбрасывает состояние задачи.
        s2.reset()
        check("reset очищает задачу", not s2.get_task_state().get("active"))
    finally:
        for p in (tmp.name, tmp.name + ".tmp"):
            try:
                os.remove(p)
            except OSError:
                pass


def test_transition_table():
    """Таблица допустимых переходов согласована с этапами."""
    section("5. Автомат: таблица переходов")
    check("все этапы описаны в таблице",
          all(s in ALLOWED_TRANSITIONS for s in STAGES), list(STAGES))
    for s in STAGES:
        for tgt in ALLOWED_TRANSITIONS[s]:
            check("переход %s->%s валиден" % (s, tgt),
                  TaskState.is_valid_transition(s, tgt))
    check("done не ведёт никуда, кроме себя",
          ALLOWED_TRANSITIONS["done"] == {"done"},
          ALLOWED_TRANSITIONS["done"])
    check("planning не прыгает в validation",
          not TaskState.is_valid_transition("planning", "validation"))


def main():
    # Гарантируем UTF-8 для вывода (см. tests/harness.ensure_utf8): иначе на
    # Windows русский текст печатается в cp1251 и при захвате вывода как
    # UTF-8 (запуск со страницы через subprocess) получаются «кракозябры».
    ensure_utf8()
    print("ТЕСТ: Состояние задачи (Task State Machine)")
    print("Сценарий: %s" % GOAL)
    test_clean_machine()
    test_pause_resume_keeps_position()
    test_prompt_block()
    test_store_persistence()
    test_transition_table()
    return finish()


if __name__ == "__main__":
    sys.exit(main())