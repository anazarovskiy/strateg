"""Финансовая модель проекта «Связь для близких и собственных устройств».

Первая услуга: сигнал организатору семейной группы о низком остатке доли участника с действием в один шаг
(добавить объём из свободного остатка) — в составе тарифа, без платы.
Единицы: подключённые группы (организатор + хотя бы один участник), номера в группах, месяцы платного обслуживания.
Горизонт: плановый год, 12 месяцев; месяц 1 — первый месяц планового года.

Что в модели из брифа (FACTS) и что принято без данных (ASSUMPTIONS): у каждого допущения есть диапазон
и способ проверки на пилоте. Прибыль считается только через удержание (дополнительные оплаченные месяцы);
подключение группы само по себе новой выручки в расчёт не приносит — данных об этом нет.
"""
import json
import math
import pathlib

HERE = pathlib.Path(__file__).resolve().parent

# ключ: (значение, единица, формулировка, запись реестра с цитатой)
FACTS = {
    "gp_base_plan": (282.0, "млн руб.", "Валовая прибыль базового года, план", "C-003"),
    "gp_base_est": (275.0, "млн руб.", "Валовая прибыль базового года, оценка", "C-003"),
    "gp_nc": (279.0, "млн руб.", "Валовая прибыль планового года без изменений", "C-003"),
    "gp_target": (285.0, "млн руб.", "Цель по валовой прибыли планового года, не ниже", "C-003"),
    "act_base": (4000, "групп", "Активные семейные группы в последнем месяце базового года, оценка", "C-004"),
    "act_nc": (5000, "групп", "Активные группы в последнем месяце планового года без изменений", "C-004"),
    "act_target": (8000, "групп", "Цель по активным группам, не ниже", "C-004"),
    "conn_start": (6500, "групп", "Подключённые группы на конец базового года (начало планового), оценка", "C-005"),
    "conn_nc": (8000, "групп", "Подключённые группы на конец планового года без изменений", "C-005"),
    "conn_target": (12000, "групп", "Цель по подключённым группам, не ниже", "C-005"),
    "own_base": (800, "владельцев", "Подключённые владельцы нескольких номеров на конец базового года, оценка", "C-006"),
    "own_nc": (1000, "владельцев", "Подключённые владельцы на конец планового года без изменений", "C-006"),
    "own_target": (2000, "владельцев", "Цель по подключённым владельцам, не ниже", "C-006"),
    "actv_base": (0.42, "доля", "Активация за 14 дней, группы последнего квартала базового года, оценка", "C-007"),
    "actv_nc": (0.45, "доля", "Активация за 14 дней в плановом году без изменений", "C-007"),
    "actv_target": (0.60, "доля", "Цель по активации за 14 дней, не ниже", "C-007"),
    "rate": (500, "руб./мес.", "Валовая прибыль за один дополнительный оплаченный месяц одного номера", "C-008"),
    "hh_avail": (100000, "домохозяйств", "Домохозяйства, доступные оператору", "C-002"),
    "own_avail": (40000, "владельцев", "Владельцы нескольких номеров, доступные оператору", "C-002"),
    "dev_pm_q2": (3.0, "чел.-мес.", "Разработка, предварительно выделено на второй квартал", "C-011"),
}

# ключ: (базовое, низкое, высокое, единица, формулировка, что хуже, почему так, как проверить)
ASSUMPTIONS = {
    "elig_share": (0.40, 0.20, 0.60, "доля",
                   "Доля подключённых групп, где можно помочь: тариф разрешает передачу, организатор вправе "
                   "распределять пакет, участник дал согласие на показ остатка", "low",
                   "в брифе нет разбивки по тарифам и согласиям; середина диапазона",
                   "в первом квартале выгрузить подключённые группы по тарифу, праву организатора и согласию"),
    "n_per_group": (3.0, 2.0, 4.0, "номеров",
                    "Оплачиваемых номеров в подключённой группе (организатор и участники)", "low",
                    "бриф говорит, что у семей с 4 и с 2 номерами доход разный, но числа не даёт",
                    "среднее по выгрузке подключённых групп"),
    "churn_m": (0.015, 0.010, 0.030, "доля в месяц",
                "Месячный отток платящих номеров из семейных групп при обычном обслуживании", "low",
                "данных об удержании в брифе нет; допущение для расчёта",
                "платежи номеров групп за последние 12 месяцев (когорты по месяцу подключения)"),
    "s_outage": (0.10, 0.03, 0.40, "доля",
                 "Доля уходов номеров, которым в предшествующие 30 дней предшествовало ограничение интернета "
                 "из-за исчерпания объёма или неоплата (то, что сигнал может предотвратить)", "low",
                 "причин ухода в брифе нет; консервативная десятая часть",
                 "связать уходы с журналом ограничений за 30 дней до ухода; решающее число для приоритета"),
    "eff": (0.25, 0.05, 0.50, "доля",
            "Доля таких уходов, которую предотвращает сигнал с действием в один шаг", "low",
            "ориентир эффекта не найден для этого рынка; задан как четверть, как в учебном примере брифа (12 → 9 %)",
            "пилот: сравнение групп с сигналом и без сигнала, затем платежи исходных групп"),
    "p_event": (0.15, 0.05, 0.30, "доля в месяц",
                "Доля подходящих групп, где за месяц случается первый подходящий случай низкого остатка", "low",
                "частоты в брифе нет",
                "посчитать по журналу остатков за три месяца до запуска (по правилу первого случая)"),
    "base_restrict": (0.12, 0.08, 0.16, "доля",
                      "Доля подходящих номеров, у которых за 7 дней после первого случая ограничили интернет, "
                      "при обычном обслуживании", "low",
                      "взято из учебного примера брифа (12 %), не измерено у этого оператора",
                      "посчитать по журналу за прошлые случаи до запуска пилота"),
    "pilot_exposed": (0.10, 0.05, 0.15, "доля",
                      "Доля подходящих групп, получающих сигнал в пилоте (вторая половина набора — контроль)", "low",
                      "чем шире пилот, тем быстрее набор, но больше риск до проверки надёжности",
                      "определяется на согласовании пилота с владельцем системы расчётов"),
    "expand_month": (7, 7, 10, "месяц",
                     "Месяц начала расширения после пилота (7 — расширяем после второго квартала; 10 — пилот "
                     "продлён на квартал)", "high",
                     "малый набор может не дать вывода к концу второго квартала (см. расчёт набора)",
                     "темп набора первых недель пилота"),
    "ramp": (4, 2, 6, "месяцев",
             "За сколько месяцев охват растёт от пилота до всех подходящих групп", "high",
             "ограничено ресурсом и поэтапным включением тарифов", "план релизов после второго квартала"),
    "uplift_active": (0.08, 0.02, 0.12, "доля, п. п.",
                      "Рост доли активных групп среди групп, получающих сигнал (просмотр остатка или выдача "
                      "объёма — действия, которые считаются активностью)", "low",
                      "сигнал сам по себе вызывает такое действие; при автоматических решениях доля может "
                      "не вырасти", "пилот: доля групп с действием за месяц в обеих ветках"),
    "uplift_activation": (0.06, 0.015, 0.10, "доля, п. п.",
                          "Рост активации за 14 дней среди новых групп, получающих подсказку первого действия", "low",
                          "потери в воронке активации не изучены; осторожная прибавка",
                          "пилот на новых группах: доля с первым действием за 14 дней"),
    "grp_exit": (0.12, 0.08, 0.18, "доля в год",
                 "Годовой выход подключённых групп (расформирование или отключение всех участников)", "low",
                 "в брифе нет; допущение для расчёта", "журнал отключений групп за 12 месяцев"),
    "accept": (0.65, 0.50, 0.80, "доля",
               "Доля созданных групп, где приглашение принято", "low",
               "пример в брифе (60 из 100) — иллюстрация, не измерение", "воронка создания группы в приложении"),
    # каналы привлечения: охват (разные подходящие организаторы, увидевшие предложение во втором полугодии),
    # доля создавших группу, доля из них, которые не пришли бы и так (прирост относительно обычного обслуживания)
    "reach_app": (25000, 10000, 40000, "домохозяйств", "Охват: предложение в приложении (второе полугодие)", "low",
                  "доступно 100 тыс. домохозяйств, подключено 6,5 тыс.; доля заходящих в приложение не известна",
                  "показ случайной части базы в пилоте"),
    "reach_sales": (6000, 2000, 10000, "домохозяйств", "Охват: предложение через сотрудников продаж", "low",
                    "по числу контактов продаж; данных нет", "согласовать с продажами план контактов"),
    "reach_support": (4000, 1500, 7000, "домохозяйств", "Охват: предложение при обращении в поддержку", "low",
                      "по числу обращений о связи в семьях; данных нет", "учёт обращений за период"),
    "create_app": (0.020, 0.008, 0.035, "доля", "Доля увидевших в приложении, кто создаёт группу", "low",
                   "осторожная доля для пассивного показа", "пилот"),
    "create_sales": (0.040, 0.015, 0.070, "доля", "Доля контактов продаж, где создаётся группа", "low",
                     "личное предложение работает сильнее показа", "пилот"),
    "create_support": (0.030, 0.010, 0.060, "доля", "Доля обращений, где создаётся группа", "low",
                       "предложение в момент проблемы уместнее", "пилот"),
    "incr_app": (0.50, 0.25, 0.70, "доля", "Какая часть созданных через приложение групп не появилась бы без показа", "low",
                 "без контрольной группы не известно", "контрольная группа без показа"),
    "incr_sales": (0.60, 0.30, 0.80, "доля", "То же для продаж", "low", "без контрольной группы не известно",
                   "контрольная группа без предложения"),
    "incr_support": (0.70, 0.40, 0.90, "доля", "То же для поддержки", "low", "без контрольной группы не известно",
                     "контрольная группа без предложения"),
    "cost_app": (2.0, 1.0, 5.0, "руб. за охват", "Расход на показ в приложении", "high", "условно", "смета"),
    "cost_sales": (300.0, 150.0, 600.0, "руб. за созданную группу", "Вознаграждение продаж за созданную группу",
                   "high", "условно; платится за все группы, не только дополнительные", "смета"),
    "cost_support": (40.0, 20.0, 80.0, "руб. за охват", "Время поддержки на одно предложение", "high",
                     "условно; считаем только как реальный расход", "хронометраж"),
    "notif_per_event": (1.5, 1.0, 3.0, "сообщений", "Сообщений организатору на один подходящий случай", "high",
                        "сигнал и напоминание", "журнал отправок"),
    "cost_notif": (0.6, 0.3, 1.5, "руб.", "Стоимость одного сообщения (смесь push и SMS)", "high", "условно",
                   "тариф рассылок"),
    "tickets": (0.003, 0.001, 0.010, "обращений на группу в месяц",
                "Новые обращения в поддержку из-за сигнала (ложные, спорные)", "high", "условно", "учёт обращений"),
    "cost_ticket": (150.0, 50.0, 300.0, "руб.", "Реальный расход на одно обращение", "high", "условно",
                    "данные финансов"),
    "rho": (0.10, 0.05, 0.25, "доля", "Корреляция исходов внутри семьи (для расчёта набора)", "high",
            "номера одной семьи делят пакет", "оценка по пилоту"),
    "ev_nums": (1.3, 1.0, 2.0, "номеров", "Номеров с подходящим случаем в группе за период набора", "low",
                "обычно один, иногда два", "журнал остатков"),
    "pm_design": (3.0, 3.0, 4.5, "чел.-мес.", "Дизайн: половина ставки на два квартала", "high",
                  "из брифа: половина ставки, не выделена", "график"),
    "pm_analyst": (3.0, 3.0, 4.5, "чел.-мес.", "Аналитика: половина ставки на два квартала", "high",
                   "из брифа: половина ставки, не выделена", "график"),
    "pm_qa": (1.0, 1.0, 2.0, "чел.-мес.", "Тестирование услуги", "high", "из брифа: 1 чел.-мес. не подтверждён",
              "график"),
    "pm_dev_simple": (2.5, 2.0, 4.0, "чел.-мес.", "Разработка простого сигнала со ссылкой на существующую операцию",
                      "high", "оценка команды не получена; допущение", "оценка разработчиков в первом квартале"),
    "pm_onboard": (2.0, 1.0, 3.0, "чел.-мес.", "Работа по подсказкам приглашения, напоминанию и первому действию", "high",
                   "оценка команды не получена; допущение", "оценка разработчиков и дизайнера в первом квартале"),
    "pm_dev_cabinet": (9.0, 6.0, 12.0, "чел.-мес.", "Разработка общего кабинета организатора", "high",
                       "оценка команды не получена; допущение", "оценка разработчиков в первом квартале"),
}

MAIN = "gp_delta"


def _expo(a, m):
    """Доля подходящих групп, получающих сигнал в месяце m (1..12)."""
    if m < 5:
        return 0.0
    if m < a["expand_month"]:
        return a["pilot_exposed"]
    p = a["pilot_exposed"]
    return p + (1 - p) * min(1.0, (m - a["expand_month"] + 1) / a["ramp"])


def _core(f, a):
    out = {}
    extra_months = 0.0
    notif_cost = 0.0
    ticket_cost = 0.0
    exits_avoided = 0.0
    nums_exposed = []
    groups_exposed = []
    quarter_profit = [0.0] * 4
    for m in range(1, 13):
        conn = f["conn_start"] + (f["conn_nc"] - f["conn_start"]) * m / 12
        e = _expo(a, m)
        g_exp = conn * a["elig_share"] * e
        n_exp = g_exp * a["n_per_group"]
        groups_exposed.append(g_exp)
        nums_exposed.append(n_exp)
        saved = n_exp * a["churn_m"] * a["s_outage"] * a["eff"]
        # спасённый номер платит каждый следующий месяц до конца года с той же вероятностью ухода
        tail = sum((1 - a["churn_m"]) ** k for k in range(1, 12 - m + 1))
        em = saved * tail
        extra_months += em
        nc = g_exp * a["p_event"] * a["notif_per_event"] * a["cost_notif"]
        tc = g_exp * a["tickets"] * a["cost_ticket"]
        notif_cost += nc
        ticket_cost += tc
        exits_avoided += g_exp * (a["grp_exit"] / 12) * a["s_outage"] * a["eff"]
        quarter_profit[(m - 1) // 3] += em * f["rate"] - nc - tc
    # привлечение: подключённые группы, пришедшие благодаря предложению
    created, incr_created, spend = 0.0, 0.0, 0.0
    for ch in ("app", "sales", "support"):
        c = a["reach_" + ch] * a["create_" + ch]
        created += c
        incr_created += c * a["incr_" + ch]
        # расход — план: объёмы охвата и создания берём базовые, чтобы слабый сценарий не выглядел дешевле
        # из-за меньшего охвата (деньги потрачены, а группы не пришли)
        pc = ASSUMPTIONS["reach_sales"][0] * ASSUMPTIONS["create_sales"][0]
        spend += (ASSUMPTIONS["reach_app"][0] * a["cost_app"] if ch == "app" else
                  pc * a["cost_sales"] if ch == "sales" else ASSUMPTIONS["reach_support"][0] * a["cost_support"])
    incr_connected = incr_created * a["accept"]
    out.update(extra_months=extra_months, notif_cost=notif_cost, ticket_cost=ticket_cost,
               exits_avoided=exits_avoided, created=created, incr_created=incr_created,
               incr_connected=incr_connected, acq_spend=spend, quarter_profit=quarter_profit,
               nums_dec=nums_exposed[-1], groups_dec=groups_exposed[-1], expo_dec=_expo(a, 12),
               expo_q4=sum(_expo(a, m) for m in (10, 11, 12)) / 3)
    return out


def _steady_year(f, a):
    """Годовая прибыль после прямых расходов при полном охвате весь год (без переноса из прошлого года)."""
    b = dict(a)
    b.update(pilot_exposed=1.0, expand_month=1)
    saved_months = 0.0
    nc = tc = 0.0
    for m in range(1, 13):
        conn = f["conn_nc"]
        g = conn * b["elig_share"]
        n = g * b["n_per_group"]
        saved = n * b["churn_m"] * b["s_outage"] * b["eff"]
        tail = sum((1 - b["churn_m"]) ** k for k in range(1, 12 - m + 1))
        saved_months += saved * tail
        nc += g * b["p_event"] * b["notif_per_event"] * b["cost_notif"]
        tc += g * b["tickets"] * b["cost_ticket"]
    return saved_months, saved_months * f["rate"] - nc - tc


def _three_year(f, a):
    """Прибыль после прямых расходов на сигнал (без привлечения) за 36 месяцев; спасённый номер платит до конца 3-го года."""
    total = 0.0
    for m in range(1, 37):
        conn = f["conn_start"] + (f["conn_nc"] - f["conn_start"]) * min(m, 12) / 12
        e = _expo(a, m) if m <= 12 else 1.0
        g = conn * a["elig_share"] * e
        n = g * a["n_per_group"]
        saved = n * a["churn_m"] * a["s_outage"] * a["eff"]
        tail = sum((1 - a["churn_m"]) ** k for k in range(1, 36 - m + 1))
        total += saved * tail * f["rate"] - g * a["p_event"] * a["notif_per_event"] * a["cost_notif"] - g * a["tickets"] * a["cost_ticket"]
    return total


def _hi(a):
    h = {}
    for k, (base, low, high, unit, text, worse, *_r) in ASSUMPTIONS.items():
        h[k] = high if worse == "low" else low
    return h


def compute(f, a):
    r = {}
    c = _core(f, a)
    k_all = list(ASSUMPTIONS.keys())
    r["extra_months"] = (c["extra_months"], "мес.", "Дополнительные оплаченные месяцы номеров за плановый год",
                         ["elig_share", "n_per_group", "churn_m", "s_outage", "eff", "expand_month", "ramp",
                          "pilot_exposed"])
    direct = c["notif_cost"] + c["ticket_cost"] + c["acq_spend"]
    r["direct_costs"] = (direct, "руб.", "Новые прямые расходы планового года (сообщения, обращения, привлечение)",
                         ["p_event", "notif_per_event", "cost_notif", "tickets", "cost_ticket", "cost_app",
                          "cost_sales", "cost_support", "reach_app", "reach_sales", "reach_support", "create_app",
                          "create_sales", "create_support", "elig_share", "expand_month", "ramp", "pilot_exposed"])
    gp_gross = c["extra_months"] * f["rate"]
    r["gp_extra"] = (gp_gross, "руб.", "Вклад дополнительных оплаченных месяцев (месяцы × 500 руб.)",
                     ["extra_months", "rate"])
    r["gp_delta"] = (gp_gross - direct, "руб.", "Прирост валовой прибыли к сценарию без изменений после прямых расходов",
                     ["gp_extra", "direct_costs"])
    r["gp_year"] = (f["gp_nc"] * 1e6 + gp_gross - direct, "руб.", "Валовая прибыль планового года с инициативой",
                    ["gp_delta", "gp_nc"])
    r["gp_gap_to_target"] = (f["gp_target"] * 1e6 - (f["gp_nc"] * 1e6 + gp_gross - direct), "руб.",
                             "Разрыв валовой прибыли с целью 285 млн руб. после инициативы", ["gp_year", "gp_target"])
    # граница решения
    need_months = (6e6 + direct) / f["rate"]
    r["be_months"] = (need_months, "мес.", "Дополнительные оплаченные месяцы, нужные для цели 285 млн руб. при тех же расходах",
                      ["direct_costs", "rate", "gp_nc", "gp_target"])
    r["be_numbers_year"] = (need_months / 12, "номеров", "Столько номеров нужно удержать на весь год ради цели",
                            ["be_months"])
    n_full = f["conn_nc"] * a["elig_share"] * a["n_per_group"]
    r["be_share_exposed"] = (need_months / 12 / n_full, "доля",
                             "Какую долю всех охваченных номеров пришлось бы удержать на год ради цели (при полном охвате)",
                             ["be_numbers_year", "conn_nc", "elig_share", "n_per_group"])
    r["churn_year"] = (1 - (1 - a["churn_m"]) ** 12, "доля", "Годовой отток номеров при обычном обслуживании (из месячного)",
                       ["churn_m"])
    r["gp_retention_only"] = (gp_gross - c["notif_cost"] - c["ticket_cost"], "руб.",
                              "Прирост прибыли без расходов на привлечение: удержание минус сообщения и обращения",
                              ["gp_extra", "elig_share", "p_event", "notif_per_event", "cost_notif", "tickets", "cost_ticket"])
    r["cost_per_incr"] = (c["acq_spend"] / max(c["incr_connected"], 1e-9), "руб.",
                          "Расход на привлечение на одну дополнительно подключённую группу",
                          ["direct_costs", "conn_incr"])
    incr_app = a["reach_app"] * a["create_app"] * a["incr_app"] * a["accept"]
    app_spend = ASSUMPTIONS["reach_app"][0] * a["cost_app"]
    r["conn_end_app"] = (f["conn_nc"] + incr_app + c["exits_avoided"], "групп",
                         "Подключённые группы на конец года, если привлечение только показом в приложении (рекомендуемый объём первого года)",
                         ["conn_nc", "reach_app", "create_app", "incr_app", "accept", "grp_exit", "s_outage", "eff",
                          "elig_share", "expand_month", "ramp", "pilot_exposed"])
    r["gp_delta_app"] = (gp_gross - c["notif_cost"] - c["ticket_cost"] - app_spend, "руб.",
                         "Прирост валовой прибыли к «без изменений», если привлечение только показом в приложении",
                         ["gp_extra", "elig_share", "p_event", "notif_per_event", "cost_notif", "tickets", "cost_ticket",
                          "reach_app", "cost_app"])
    r["be_multiple"] = (need_months / max(c["extra_months"], 1e-9), "раз",
                        "Во сколько раз нужно больше месяцев, чем даёт модель", ["be_months", "extra_months"])
    h = _hi(a)
    ch = _core(f, h)
    r["gp_ceiling"] = (ch["extra_months"] * f["rate"] - ch["notif_cost"] - ch["ticket_cost"] - ch["acq_spend"],
                       "руб.", "Потолок: все допущения на лучшей границе одновременно", k_all)
    # предельные вложения: прибыль после прямых расходов за 3 года
    sm, sp = _steady_year(f, a)
    r["steady_profit"] = (sp, "руб./год", "Годовая прибыль после прямых расходов при полном охвате подходящих групп (спасённый номер учтён в пределах года)",
                          ["elig_share", "n_per_group", "churn_m", "s_outage", "eff", "p_event", "notif_per_event",
                           "cost_notif", "tickets", "cost_ticket"])
    r["months_per_number_year"] = (sm / n_full, "мес. на номер в год",
                                   "Дополнительных оплаченных месяцев на один охваченный номер в год от самого сигнала (полный охват)",
                                   ["elig_share", "n_per_group", "churn_m", "s_outage", "eff"])
    y3 = _three_year(f, a)
    y3h = _three_year(f, h)
    r["max_invest_3y"] = (y3, "руб.", "Предельные вложения: прибыль после прямых расходов на сигнал за 36 месяцев, спасённый номер платит до конца третьего года, привлечение не входит",
                          k_all)
    r["max_invest_ceiling"] = (y3h, "руб.", "Потолок предельных вложений за 36 месяцев при всех допущениях на лучшей границе", k_all)
    pm_total = a["pm_design"] + a["pm_analyst"] + a["pm_qa"] + a["pm_dev_simple"]
    r["pm_simple"] = (pm_total, "чел.-мес.", "Работа первого запуска (простой сигнал): дизайн, аналитика, тестирование, разработка",
                      ["pm_design", "pm_analyst", "pm_qa", "pm_dev_simple"])
    r["pm_cost_limit"] = (max(0.0, r["max_invest_3y"][0]) / pm_total, "руб.",
                          "Стоимость одного человеко-месяца, при которой простой сигнал окупится за три года",
                          ["max_invest_3y", "pm_simple"])
    r["pm_cost_limit_ceiling"] = (y3h / pm_total, "руб.",
                                  "Стоимость человеко-месяца, при которой простой сигнал окупится за три года даже на лучших границах",
                                  ["max_invest_ceiling", "pm_simple"])
    r["dev_deficit_cabinet"] = (a["pm_dev_cabinet"] - f["dev_pm_q2"], "чел.-мес.",
                                "Нехватка разработки для общего кабинета против 3 выделенных человеко-месяцев",
                                ["pm_dev_cabinet", "dev_pm_q2"])
    r["dev_margin_simple"] = (f["dev_pm_q2"] - a["pm_dev_simple"], "чел.-мес.",
                              "Запас разработки для простого сигнала против 3 выделенных человеко-месяцев",
                              ["pm_dev_simple", "dev_pm_q2"])
    # показатели таблицы брифа
    conn_end = f["conn_nc"] + c["incr_connected"] + c["exits_avoided"]
    r["conn_end"] = (conn_end, "групп", "Подключённые группы на конец года с инициативой",
                     ["conn_nc", "reach_app", "reach_sales", "reach_support", "create_app", "create_sales",
                      "create_support", "incr_app", "incr_sales", "incr_support", "accept", "grp_exit", "s_outage",
                      "eff", "elig_share", "expand_month", "ramp", "pilot_exposed"])
    r["conn_incr"] = (c["incr_connected"], "групп", "Из них дополнительно привлечённые группы (после приёма приглашения)",
                      ["reach_app", "reach_sales", "reach_support", "create_app", "create_sales", "create_support",
                       "incr_app", "incr_sales", "incr_support", "accept"])
    r["conn_exits"] = (c["exits_avoided"], "групп", "Из них сохранённые группы (меньше выходов)",
                       ["grp_exit", "s_outage", "eff", "elig_share", "expand_month", "ramp", "pilot_exposed"])
    r["conn_gap"] = (f["conn_target"] - conn_end, "групп", "Разрыв подключённых групп с целью 12 000", ["conn_end", "conn_target"])
    share_nc = f["act_nc"] / f["conn_nc"]
    act_share = share_nc + c["expo_dec"] * a["elig_share"] * a["uplift_active"]
    act_end = conn_end * act_share
    r["act_share"] = (act_share, "доля", "Доля активных среди подключённых групп в декабре с инициативой",
                      ["act_nc", "conn_nc", "elig_share", "uplift_active", "expand_month", "ramp", "pilot_exposed"])
    r["act_end"] = (act_end, "групп", "Активные группы в последнем месяце года с инициативой", ["conn_end", "act_share"])
    r["act_gap"] = (f["act_target"] - act_end, "групп", "Разрыв активных групп с целью 8 000", ["act_end", "act_target"])
    r["act_from_count"] = ((conn_end - f["conn_nc"]) * share_nc, "групп", "Прирост активных за счёт большего числа групп",
                           ["conn_end", "conn_nc", "act_nc"])
    r["act_from_share"] = (conn_end * (act_share - share_nc), "групп", "Прирост активных за счёт большей доли активных",
                           ["conn_end", "act_share", "act_nc"])
    actv = f["actv_nc"] + c["expo_q4"] * a["elig_share"] * a["uplift_activation"]
    r["actv_end"] = (actv, "доля", "Активация за 14 дней, группы последнего квартала, с инициативой",
                     ["actv_nc", "elig_share", "uplift_activation", "expand_month", "ramp", "pilot_exposed"])
    r["actv_gap_pp"] = ((f["actv_target"] - actv) * 100, "п. п.", "Разрыв активации с целью 60 %", ["actv_end", "actv_target"])
    r["own_end"] = (f["own_nc"], "владельцев", "Подключённые владельцы на конец года: обслуживание не меняем", ["own_nc"])
    r["own_gap"] = (f["own_target"] - f["own_nc"], "владельцев", "Разрыв по владельцам с целью 2 000", ["own_nc", "own_target"])
    r["own_gap_share"] = (f["own_nc"] / f["own_avail"], "доля", "Доля подключённых от 40 тыс. доступных владельцев (прогноз)",
                          ["own_nc", "own_avail"])
    # во сколько раз пришлось бы расширить привлечение, чтобы закрыть разрыв по группам (нижняя оценка цены)
    mult = (f["conn_target"] - conn_end) / max(c["incr_connected"], 1e-9)
    r["conn_close_mult"] = (mult, "раз", "Во сколько раз шире нужно привлечение, чтобы закрыть разрыв при той же отдаче канала",
                            ["conn_gap", "conn_incr"])
    r["conn_close_cost"] = (mult * c["acq_spend"], "руб.", "Нижняя оценка расхода на привлечение для закрытия разрыва (отдача канала при росте только падает)",
                            ["conn_close_mult", "direct_costs"])
    # --- цена устранения разрывов -------------------------------------------------------------------------
    pool = f["hh_avail"] - f["conn_start"]  # доступные домохозяйства без подключённой группы
    r["pool"] = (pool, "домохозяйств", "Доступные домохозяйства без подключённой группы на начало года", ["conn_start"])
    ch_conv = {ch: a["create_" + ch] * a["incr_" + ch] * a["accept"] for ch in ("app", "sales", "support")}
    spend_ch = {"app": a["cost_app"] / a["create_app"],
                "sales": a["cost_sales"] / (a["incr_sales"] * a["accept"]),
                "support": a["cost_support"] / ch_conv["support"]}
    for ch, text in (("app", "показ в приложении"), ("sales", "продажи"), ("support", "поддержка")):
        cpi = spend_ch[ch] if ch != "app" else a["cost_app"] / ch_conv["app"]
        keys = ["cost_app", "create_app", "incr_app", "accept"] if ch == "app" else \
               (["cost_sales", "incr_sales", "accept"] if ch == "sales" else
                ["cost_support", "create_support", "incr_support", "accept"])
        r["cpi_" + ch] = (cpi, "руб.", f"Расход на одну дополнительно подключённую группу: {text}", keys)
        r["be_grp_" + ch] = (cpi / f["rate"], "мес.",
                             f"Сколько дополнительных оплаченных месяцев должна дать подключённая группа, чтобы окупить канал «{text}» (по 500 руб.)",
                             ["cpi_" + ch, "rate"])
    best = max(ch_conv.values())
    conn_max_acq = pool * best
    exits_all = (f["conn_start"] + f["conn_nc"]) / 2 * a["grp_exit"]
    conn_max = f["conn_nc"] + conn_max_acq + exits_all
    r["conn_max_acq"] = (conn_max_acq, "групп", "Предел привлечения: всё доступное домохозяйство без группы получает самое результативное предложение (продажи)",
                         ["pool", "create_sales", "create_app", "create_support", "incr_sales", "incr_app", "incr_support", "accept"])
    r["conn_exits_all"] = (exits_all, "групп", "Предел сохранения: ни одна группа не выходит за год", ["conn_start", "conn_nc", "grp_exit"])
    r["conn_max"] = (conn_max, "групп", "Подключённые группы на конец года при обоих пределах сразу", ["conn_nc", "conn_max_acq", "conn_exits_all"])
    r["conn_gap_min"] = (f["conn_target"] - conn_max, "групп", "Разрыв с целью 12 000 даже при обоих пределах", ["conn_max", "conn_target"])
    r["conn_max_cost"] = (pool * a["create_sales"] * a["cost_sales"], "руб.", "Расход на предел привлечения через продажи (вознаграждение за созданную группу)",
                          ["pool", "create_sales", "cost_sales"])
    share_nc = f["act_nc"] / f["conn_nc"]
    need_share = f["act_target"] / conn_max
    r["act_need_share"] = (need_share, "доля", "Доля активных, нужная для цели 8 000 при обоих пределах по группам", ["act_target", "conn_max"])
    r["act_need_uplift"] = ((need_share - share_nc) * 100 / a["elig_share"], "п. п.",
                            "Рост доли активных среди получающих сигнал (при охвате всех подходящих), нужный для цели 8 000 при обоих пределах",
                            ["act_need_share", "act_nc", "conn_nc", "elig_share"])
    r["act_need_share_base"] = (f["act_target"] / conn_end, "доля", "Доля активных, нужная для цели 8 000 при прогнозе подключённых с инициативой", ["act_target", "conn_end"])
    created = (f["conn_nc"] - f["conn_start"] + exits_all) / a["accept"]
    r["created_year"] = (created, "групп", "Созданных групп за год (новые подключённые плюс выбывшие, делённые на долю принятых приглашений)",
                         ["conn_nc", "conn_start", "grp_exit", "accept"])
    r["actv_gap_groups"] = (created * (f["actv_target"] - actv), "групп", "Групп, которые надо дополнительно активировать за 14 дней для цели 60 %",
                            ["created_year", "actv_target", "actv_end"])
    r["actv_need_action"] = (f["actv_target"] / a["accept"], "доля", "Первое действие среди групп с принятым приглашением, нужное для цели 60 % при базовом приёме", ["actv_target", "accept"])
    r["actv_need_action_hi"] = (f["actv_target"] / 0.80, "доля", "То же при приёме приглашений 80 % (верхняя граница)", ["actv_target"])
    r["actv_base_action"] = (f["actv_nc"] / a["accept"], "доля", "Первое действие среди групп с принятым приглашением сейчас (45 % / 65 %)", ["actv_nc", "accept"])
    r["onboard_pm_per_group"] = (a["pm_onboard"] / max(created * (f["actv_target"] - actv), 1e-9), "чел.-мес. на группу",
                                 "Работа по подсказкам приглашения и первого действия на одну дополнительно активированную группу; умножить на цену человеко-месяца и поделить на 500 руб. — нужные месяцы удержания",
                                 ["pm_onboard", "actv_gap_groups"])
    # пилот: набор и чувствительность
    p0 = a["base_restrict"]
    n_arm = (2.8 ** 2) * (p0 * (1 - p0) + (p0 - 0.03) * (1 - p0 + 0.03)) / (0.03 ** 2)
    deff = 1 + (a["ev_nums"] - 1) * a["rho"]
    n_arm *= deff
    groups_total = 2 * n_arm / a["ev_nums"]
    conn_m5 = f["conn_start"] + (f["conn_nc"] - f["conn_start"]) * 5 / 12
    inflow_week = conn_m5 * a["elig_share"] * a["p_event"] / 4.33
    r["pilot_n_arm"] = (n_arm, "номеров", "Подходящих номеров на ветку для различия 12 % → 9 % (80 % мощность, 5 % значимость)",
                        ["base_restrict", "ev_nums", "rho"])
    r["pilot_groups"] = (groups_total, "групп", "Подходящих групп на оба варианта для такой проверки",
                         ["pilot_n_arm", "ev_nums"])
    r["pilot_inflow"] = (inflow_week, "групп/нед.", "Впервые подходящих групп в неделю при включении всех подходящих",
                         ["conn_start", "conn_nc", "elig_share", "p_event"])
    r["pilot_weeks"] = (groups_total / max(inflow_week, 1e-9), "недель", "Недель набора при включении всех подходящих групп",
                        ["pilot_groups", "pilot_inflow"])
    # что успеет стать известно к концу второго квартала: набор 6 недель
    g6 = inflow_week * 6
    n6 = g6 / 2 * a["ev_nums"] / deff
    mde = 2.8 * math.sqrt(2 * p0 * (1 - p0) / max(n6, 1e-9))
    r["pilot_groups_q2"] = (g6, "групп", "Подходящих групп, набранных за 6 недель до конца второго квартала", ["pilot_inflow"])
    r["pilot_mde_q2"] = (mde * 100, "п. п.", "Наименьшее различие, заметное по итогам 6 недель набора", ["pilot_groups_q2", "base_restrict", "ev_nums", "rho"])
    r["pilot_exposed_groups"] = (conn_m5 * a["elig_share"] * a["pilot_exposed"], "групп", "Подходящих групп, получающих сигнал в пилоте",
                                 ["conn_start", "conn_nc", "elig_share", "pilot_exposed"])
    if not a.get("_nohi"):
        rh = compute(f, dict(h, _nohi=True))
        for key, text in (("conn_end", "Подключённые группы на конец года"), ("act_end", "Активные группы в последнем месяце года"),
                          ("actv_end", "Активация за 14 дней")):
            r[key + "_ceiling"] = (rh[key][0], rh[key][1], text + " при всех допущениях на лучшей границе (потолок инициативы)", k_all)
    return r


# --- служебная часть: обычно менять не нужно ---------------------------------------------------------------

def _values(scenario):
    f = {k: v[0] for k, v in FACTS.items()}
    a = {}
    for k, (base, low, high, *_rest) in ASSUMPTIONS.items():
        worse = ASSUMPTIONS[k][5]
        if scenario == "base":
            a[k] = base
        elif scenario == "weak":
            a[k] = low if worse == "low" else high
        elif scenario == "strong":
            a[k] = high if worse == "low" else low
    return f, a


def run(scenario="base", override=None):
    f, a = _values(scenario)
    a.update(override or {})
    return compute(f, a)


def breakeven(key, result_key, target, lo, hi, steps=60):
    """Какое значение допущения key даёт result_key = target (остальное — базовое). Бисекция."""
    def val(x):
        return run("base", {key: x})[result_key][0] - target
    a, b = lo, hi
    fa, fb = val(a), val(b)
    if fa * fb > 0:
        return None
    for _ in range(steps):
        m = (a + b) / 2
        fm = val(m)
        if fa * fm <= 0:
            b, fb = m, fm
        else:
            a, fa = m, fm
    return (a + b) / 2


def payback(profit_by_q, invest_by_q):
    """Квартал, в котором накопленная прибыль после прямых расходов покрыла накопленные вложения (1..n)."""
    cp = ci = 0
    for i, (p, c) in enumerate(zip(profit_by_q, invest_by_q), 1):
        cp, ci = cp + p, ci + c
        if cp >= ci and ci > 0:
            return i
    return None


def _num(v):
    if isinstance(v, float) and not v.is_integer():
        if abs(v) < 10:
            return f"{v:,.3f}".replace(",", " ").replace(".", ",").rstrip("0").rstrip(",")
        return f"{v:,.1f}".replace(",", " ").replace(".", ",").rstrip("0").rstrip(",")
    return f"{int(round(v)):,}".replace(",", " ")


def main():
    base, weak = run("base"), run("weak")
    results = []
    for k, (v, unit, text, basis) in base.items():
        results.append({"key": k, "value": v, "unit": unit, "text": text, "basis": basis, "scenario": "базовый"})
    for k, (v, unit, text, basis) in weak.items():
        wb = [b + "_weak" if b in weak else b for b in basis]  # слабый результат опирается на слабые результаты
        results.append({"key": k + "_weak", "value": v, "unit": unit, "text": text, "basis": wb,
                        "scenario": "слабый"})
    tables = []
    rows = ["| Показатель | Базовый | Слабый | Единица |", "|---|---|---|---|"]
    for k, (v, unit, text, _b) in base.items():
        rows.append(f"| {text} | {_num(v)} | {_num(weak[k][0])} | {unit} |")
    tables.append({"title": "Сценарии", "markdown": "\n".join(rows)})
    if MAIN and MAIN in base:
        rows = ["| Допущение | Низкое → результат | Высокое → результат |", "|---|---|---|"]
        spread = []
        for k, (b0, lo, hi, unit, text, *_r) in ASSUMPTIONS.items():
            r_lo, r_hi = run("base", {k: lo})[MAIN][0], run("base", {k: hi})[MAIN][0]
            spread.append((abs(r_hi - r_lo), f"| {text} | {_num(lo)} → {_num(r_lo)} | {_num(hi)} → {_num(r_hi)} |"))
        rows += [r for _, r in sorted(spread, reverse=True)]
        tables.append({"title": f"Чувствительность: {base[MAIN][2]}", "markdown": "\n".join(rows)})
    # помесячная прибыль по кварталам (для плана)
    f, a = _values("base")
    qp = _core(f, a)["quarter_profit"]
    tables.append({"title": "Прибыль после прямых расходов по кварталам (без расходов на привлечение), руб.",
                   "markdown": "| Кв. 1 | Кв. 2 | Кв. 3 | Кв. 4 |\n|---|---|---|---|\n| " + " | ".join(_num(x) for x in qp) + " |"})
    model = {
        "facts": [{"key": k, "value": v[0], "unit": v[1], "text": v[2], "claim": v[3]} for k, v in FACTS.items()],
        "assumptions": [{"key": k, "value": v[0], "low": v[1], "high": v[2], "unit": v[3], "text": v[4],
                         "why": v[6], "check": v[7]} for k, v in ASSUMPTIONS.items()],
        "results": results,
        "tables": tables,
    }
    (HERE / "model.json").write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
    for t in tables:
        print(f"\n## {t['title']}\n\n{t['markdown']}")
    print("\nСохранено: analysis/model.json. Дальше: ./strat model sync")


if __name__ == "__main__":
    main()
