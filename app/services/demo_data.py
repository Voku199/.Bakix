"""Static demo data returned when session['is_demo'] is True."""

# Raw format used by index() for the grade-trend chart (needs MarkDate).
DEMO_SUBJECTS_RAW = [
    {
        "Subject": {"Id": "MAT", "Name": "Matematika", "Abbrev": "M"},
        "AverageText": "2,17",
        "Marks": [
            {"MarkText": "1", "Weight": 1, "Caption": "Test – rovnice", "IsPoints": False,
             "MarkDate": "2025-10-08", "EditDate": "2025-10-08"},
            {"MarkText": "2", "Weight": 1, "Caption": "Písemka – funkce", "IsPoints": False,
             "MarkDate": "2025-11-20", "EditDate": "2025-11-20"},
            {"MarkText": "3", "Weight": 2, "Caption": "Čtvrtletní písemka", "IsPoints": False,
             "MarkDate": "2026-01-15", "EditDate": "2026-01-15"},
            {"MarkText": "2", "Weight": 1, "Caption": "Ústní zkoušení", "IsPoints": False,
             "MarkDate": "2026-03-12", "EditDate": "2026-03-12"},
            {"MarkText": "2", "Weight": 1, "Caption": "Test – derivace", "IsPoints": False,
             "MarkDate": "2026-05-07", "EditDate": "2026-05-07"},
        ],
    },
    {
        "Subject": {"Id": "CJL", "Name": "Český jazyk", "Abbrev": "ČJL"},
        "AverageText": "1,67",
        "Marks": [
            {"MarkText": "1", "Weight": 1, "Caption": "Slohová práce", "IsPoints": False,
             "MarkDate": "2025-10-22", "EditDate": "2025-10-22"},
            {"MarkText": "2", "Weight": 1, "Caption": "Diktát", "IsPoints": False,
             "MarkDate": "2025-12-05", "EditDate": "2025-12-05"},
            {"MarkText": "2", "Weight": 1, "Caption": "Test – literatura", "IsPoints": False,
             "MarkDate": "2026-02-18", "EditDate": "2026-02-18"},
            {"MarkText": "1", "Weight": 2, "Caption": "Maturitní sloh (cvičný)", "IsPoints": False,
             "MarkDate": "2026-04-10", "EditDate": "2026-04-10"},
        ],
    },
    {
        "Subject": {"Id": "ANJ", "Name": "Anglický jazyk", "Abbrev": "ANJ"},
        "AverageText": "1,50",
        "Marks": [
            {"MarkText": "1", "Weight": 2, "Caption": "Speaking test", "IsPoints": False,
             "MarkDate": "2025-11-05", "EditDate": "2025-11-05"},
            {"MarkText": "2", "Weight": 1, "Caption": "Grammar test", "IsPoints": False,
             "MarkDate": "2026-01-28", "EditDate": "2026-01-28"},
            {"MarkText": "1", "Weight": 1, "Caption": "Reading comprehension", "IsPoints": False,
             "MarkDate": "2026-03-25", "EditDate": "2026-03-25"},
            {"MarkText": "2", "Weight": 1, "Caption": "Writing – esej", "IsPoints": False,
             "MarkDate": "2026-05-14", "EditDate": "2026-05-14"},
        ],
    },
    {
        "Subject": {"Id": "FYZ", "Name": "Fyzika", "Abbrev": "F"},
        "AverageText": "2,50",
        "Marks": [
            {"MarkText": "2", "Weight": 1, "Caption": "Test – mechanika", "IsPoints": False,
             "MarkDate": "2025-10-30", "EditDate": "2025-10-30"},
            {"MarkText": "3", "Weight": 2, "Caption": "Pololetní písemka", "IsPoints": False,
             "MarkDate": "2026-01-22", "EditDate": "2026-01-22"},
            {"MarkText": "2", "Weight": 1, "Caption": "Laboratorní zpráva", "IsPoints": False,
             "MarkDate": "2026-04-02", "EditDate": "2026-04-02"},
            {"MarkText": "3", "Weight": 1, "Caption": "Test – elektřina", "IsPoints": False,
             "MarkDate": "2026-05-20", "EditDate": "2026-05-20"},
        ],
    },
    {
        "Subject": {"Id": "INF", "Name": "Informatika", "Abbrev": "INF"},
        "AverageText": "1,00",
        "Marks": [
            {"MarkText": "1", "Weight": 1, "Caption": "Projekt – databáze", "IsPoints": False,
             "MarkDate": "2025-11-15", "EditDate": "2025-11-15"},
            {"MarkText": "1", "Weight": 1, "Caption": "Test – sítě", "IsPoints": False,
             "MarkDate": "2026-02-10", "EditDate": "2026-02-10"},
            {"MarkText": "1", "Weight": 2, "Caption": "Semestrální projekt", "IsPoints": False,
             "MarkDate": "2026-04-28", "EditDate": "2026-04-28"},
        ],
    },
    {
        "Subject": {"Id": "CHE", "Name": "Chemie", "Abbrev": "CH"},
        "AverageText": "3,00",
        "Marks": [
            {"MarkText": "3", "Weight": 1, "Caption": "Test – názvosloví", "IsPoints": False,
             "MarkDate": "2025-12-10", "EditDate": "2025-12-10"},
            {"MarkText": "4", "Weight": 1, "Caption": "Ústní zkoušení", "IsPoints": False,
             "MarkDate": "2026-02-25", "EditDate": "2026-02-25"},
            {"MarkText": "2", "Weight": 1, "Caption": "Test – organická chem.", "IsPoints": False,
             "MarkDate": "2026-04-16", "EditDate": "2026-04-16"},
        ],
    },
    {
        "Subject": {"Id": "DEJ", "Name": "Dějepis", "Abbrev": "D"},
        "AverageText": "1,50",
        "Marks": [
            {"MarkText": "1", "Weight": 1, "Caption": "Test – 1. světová válka", "IsPoints": False,
             "MarkDate": "2025-11-28", "EditDate": "2025-11-28"},
            {"MarkText": "2", "Weight": 1, "Caption": "Referát – 2. sv. válka", "IsPoints": False,
             "MarkDate": "2026-03-05", "EditDate": "2026-03-05"},
        ],
    },
]

# Reformatted marks for /api/3/marks (matches what api_marks() normally returns).
DEMO_MARKS_API = [
    {
        "Subject": {"Name": s["Subject"]["Name"], "Abbrev": s["Subject"]["Abbrev"]},
        "AverageText": s["AverageText"],
        "Marks": [
            {
                "MarkText": m["MarkText"],
                "Weight":   m["Weight"],
                "Caption":  m["Caption"],
                "IsPoints": m["IsPoints"],
                "EditDate": m["EditDate"],
            }
            for m in s["Marks"]
        ],
    }
    for s in DEMO_SUBJECTS_RAW
]

DEMO_HOMEWORKS = [
    {
        "ID":             "demo-hw-1",
        "Subject":        "Matematika",
        "Content":        "Vypracovat cvičení 4.5 – derivace složené funkce (str. 87–89)",
        "DateEnd":        "2026-06-03T23:59:00+01:00",
        "HasAttachments": False,
    },
    {
        "ID":             "demo-hw-2",
        "Subject":        "Anglický jazyk",
        "Content":        "Přečíst článek o klimatické změně a připravit krátkou prezentaci (3 min.)",
        "DateEnd":        "2026-06-05T23:59:00+01:00",
        "HasAttachments": True,
    },
    {
        "ID":             "demo-hw-3",
        "Subject":        "Český jazyk",
        "Content":        "Napsat slohovou práci na téma: Město nebo vesnice? (min. 300 slov)",
        "DateEnd":        "2026-06-10T23:59:00+01:00",
        "HasAttachments": False,
    },
]

DEMO_KOMENS = [
    {
        "Id":       "demo-msg-1",
        "Title":    "Třídní schůzky – pozvánka",
        "Sender":   "Mgr. Jana Nováková",
        "SentDate": "2026-05-20T10:00:00+01:00",
        "Read":     True,
        "Text":     "Vážení rodiče, dovolujeme si Vás pozvat na třídní schůzky 12. června 2026 od 17:00 v učebně č. 203.",
    },
    {
        "Id":       "demo-msg-2",
        "Title":    "Školní výlet – organizační informace",
        "Sender":   "Mgr. Petr Dvořák",
        "SentDate": "2026-05-22T08:30:00+01:00",
        "Read":     True,
        "Text":     "Školní výlet proběhne 5. června. Sraz je v 7:45 před školou. Nezapomeňte svačinu a pláštěnku.",
    },
    {
        "Id":       "demo-msg-3",
        "Title":    "Nová učebnice matematiky",
        "Sender":   "Mgr. Eva Procházková",
        "SentDate": "2026-05-27T14:15:00+01:00",
        "Read":     False,
        "Text":     "Od příštího týdne budeme pracovat z nové učebnice. Prosím zakupte ji do pátku. ISBN: 978-80-7235-123-4.",
    },
]

DEMO_ABSENCES = {
    "Absences": [
        {"Date": "2026-04-07", "SchoolAbs": 2, "DistanceAbs": 0, "LateAbs": 0, "SoonAbs": 0, "SchoolAbsCount": 2},
        {"Date": "2026-03-18", "SchoolAbs": 4, "DistanceAbs": 0, "LateAbs": 0, "SoonAbs": 0, "SchoolAbsCount": 4},
        {"Date": "2026-02-05", "SchoolAbs": 6, "DistanceAbs": 0, "LateAbs": 0, "SoonAbs": 0, "SchoolAbsCount": 6},
    ],
    "Summary": {
        "SchoolAbs":   12,
        "DistanceAbs":  0,
        "LateAbs":      1,
        "SoonAbs":      0,
    },
}

DEMO_TIMETABLE_TODAY = [
    {"hour": 1, "subject": "Matematika",      "teacher": "Mgr. Procházková E.", "time": "08:00-08:45", "room": "203",  "status": "OK",           "change_info": None},
    {"hour": 2, "subject": "Český jazyk",     "teacher": "Mgr. Nováková J.",    "time": "08:55-09:40", "room": "101",  "status": "OK",           "change_info": None},
    {"hour": 3, "subject": "Anglický jazyk",  "teacher": "Mgr. Smith R.",       "time": "09:50-10:35", "room": "105",  "status": "OK",           "change_info": None},
    {"hour": 4, "subject": "Fyzika",          "teacher": "Mgr. Horáček V.",     "time": "10:55-11:40", "room": "Lab1", "status": "Substitution", "change_info": "Supluje Mgr. Horáček (za Mgr. Kováře)"},
    {"hour": 5, "subject": "Informatika",     "teacher": "Ing. Blažek M.",      "time": "11:50-12:35", "room": "PC2",  "status": "OK",           "change_info": None},
    {"hour": 6, "subject": "Tělesná výchova", "teacher": "Mgr. Krejčí P.",      "time": "12:45-13:30", "room": "Těl.", "status": "OK",           "change_info": None},
]

DEMO_TIMETABLE_TOMORROW = [
    {"hour": 1, "subject": "Chemie",         "teacher": "Mgr. Marková L.",     "time": "08:00-08:45", "room": "Lab2", "status": "OK", "change_info": None},
    {"hour": 2, "subject": "Dějepis",        "teacher": "Mgr. Dvořák P.",      "time": "08:55-09:40", "room": "102",  "status": "OK", "change_info": None},
    {"hour": 3, "subject": "Matematika",     "teacher": "Mgr. Procházková E.", "time": "09:50-10:35", "room": "203",  "status": "OK", "change_info": None},
    {"hour": 4, "subject": "Anglický jazyk", "teacher": "Mgr. Smith R.",       "time": "10:55-11:40", "room": "105",  "status": "OK", "change_info": None},
    {"hour": 5, "subject": "Český jazyk",    "teacher": "Mgr. Nováková J.",    "time": "11:50-12:35", "room": "101",  "status": "OK", "change_info": None},
]
