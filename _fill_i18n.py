# One-off: fill EN translations for new welcome.html strings + drifted onboarding
# strings, clear fuzzy flags, then rewrite the .po. Delete after use.
import io
from babel.messages.pofile import read_po, write_po

PO = "translations/en/LC_MESSAGES/messages.po"

T = {
    # ── onboarding drift (was never extracted before) ──────────────
    "Heslo nikdy nevidíme": "We never see your password",
    "Vaše heslo ukládáme pouze v zašifrované podobě — nikdo z Bakixu ho nemůže přečíst. Slouží výhradně k automatickému obnovení přístupu, pokud vás Bakaláře odhlásí.":
        "Your password is stored only in encrypted form — nobody at Bakix can read it. It is used solely to automatically restore access when Bakaláři logs you out.",
    "Funguje jako mobilní aplikace": "Works as a mobile app",
    "Vyhledejte svou školu — nebo zadejte URL ručně.": "Find your school — or enter the URL manually.",
    "Vyhledat školu": "Find school",
    "Město nebo název školy…": "City or school name…",
    "nebo zadat URL ručně": "or enter URL manually",
    "Ztlumit hudbu": "Mute music",
    "Hudba": "Music",

    # ── welcome: head / SEO ─────────────────────────────────────────
    "Škola. Konečně přehledně.": "School. Finally clear.",
    "Bakix je AI studijní asistent pro české studenty napojený na Bakaláře. Přehledný dashboard se známkami, rozvrhem, úkoly a Komens zprávami. Zdarma, bez registrace.":
        "Bakix is an AI study assistant for Czech students connected to Bakaláři. A clean dashboard with grades, timetable, homework and Komens messages. Free, no registration.",
    "AI studijní asistent pro české studenty. Přehled známek, rozvrhu a úkolů z Bakalářů. Zdarma.":
        "An AI study assistant for Czech students. Grades, timetable and homework from Bakaláři at a glance. Free.",

    # ── welcome: hero ───────────────────────────────────────────────
    "AI studijní asistent pro Bakaláře": "AI study assistant for Bakaláři",
    "Konečně": "Finally",
    "Bakix se připojí na <strong>Bakaláře</strong> a promění známky, rozvrh, úkoly i zprávy v jeden přehledný dashboard. S AI asistentem, který tě <strong>fakt naučí</strong>.":
        "Bakix connects to <strong>Bakaláři</strong> and turns grades, timetable, homework and messages into one clean dashboard. With an AI assistant that <strong>actually teaches you</strong>.",
    "Vyzkoušet demo": "Try the demo",
    "Co všechno umí": "See what it can do",
    "Demo nevyžaduje žádné údaje — jedním klikem": "The demo needs no credentials — one click",
    "PWA — mobil i desktop": "PWA — mobile & desktop",
    "Bakaláři API": "Bakaláři API",
    "Matematika": "Mathematics",
    "Čeština": "Czech",
    "Fyzika": "Physics",
    "Odpadlo": "Cancelled",
    "Fyzika — protokol": "Physics — lab report",
    "Dějepis — referát": "History — presentation",
    "Průměr": "Average",
    "Zítra píšeš fyziku — chceš si to projet?": "You have a physics test tomorrow — want to review it?",

    # ── welcome: grades section ─────────────────────────────────────
    "Jeden pohled.": "One glance.",
    "Žádné proklikávání Bakalářů. Průměry, váhy, trendy — všechno přepočítané a seřazené. <strong>Nová známka? Víš o ní dřív, než dojdeš ze třídy.</strong>":
        "No more clicking through Bakaláři. Averages, weights, trends — all computed and sorted. <strong>New grade? You know before you leave the classroom.</strong>",
    "Vážené průměry": "Weighted averages",
    "Trend grafy": "Trend charts",
    "Kalkulačka „co potřebuju na jedničku“": "“What do I need for an A” calculator",
    "Live z Bakalářů": "Live from Bakaláři",
    "Graf vývoje průměru": "Average trend chart",
    "↘ průměr klesá = zlepšuješ se": "↘ average dropping = you are improving",

    # ── welcome: timetable section ──────────────────────────────────
    "Víš, co tě čeká.": "Know what is coming.",
    "Dřív než zazvoní.": "Before the bell rings.",
    "Dnešek a zítřek na jedno klepnutí. Suplování, odpadlé hodiny a změny učeben <strong>zvýrazněné dřív, než si jich všimne třída</strong>.":
        "Today and tomorrow one tap away. Substitutions, cancelled lessons and room changes <strong>highlighted before your class notices</strong>.",
    "Dnes / zítra přepínač": "Today / tomorrow switch",
    "Suplování": "Substitution",
    "Push při změně": "Push on changes",
    "Úterý · 6 hodin": "Tuesday · 6 lessons",
    "uč.": "room",
    "odpadá": "cancelled",
    "Angličtina": "English",
    "Dějepis": "History",

    # ── welcome: homework section ───────────────────────────────────
    "Deadliny tě": "Deadlines will not",
    "už nepřekvapí.": "surprise you again.",
    "Domácí úkoly seřazené podle odevzdání. Co hoří, svítí červeně. <strong>A večer ti přijde připomínka</strong> — takže ráno žádná panika.":
        "Homework sorted by due date. What is urgent glows red. <strong>And an evening reminder arrives</strong> — so no morning panic.",
    "Řazení podle deadline": "Sorted by deadline",
    "Přílohy": "Attachments",
    "Večerní souhrn v push": "Evening push summary",
    "Domácí úkoly": "Homework",
    "4 aktivní": "4 active",
    "Fyzika — laboratorní protokol": "Physics — laboratory report",
    "měření hustoty": "density measurement",
    "Matematika — pracovní list": "Maths — worksheet",
    "kvadratické rovnice": "quadratic equations",
    "Pátek": "Friday",
    "první republika": "the First Republic",
    "Příští týden": "Next week",
    "Angličtina — esej": "English — essay",

    # ── welcome: messages section ───────────────────────────────────
    "Zprávy od učitelů.": "Messages from teachers.",
    "Bez lovení.": "No hunting around.",
    "Komens zprávy přehledně na dashboardu — nepřečtené nahoře, <strong>odpovědět můžeš rovnou z Bakixu</strong>. A o ničem důležitém se nedozvíš poslední.":
        "Komens messages right on the dashboard — unread first, <strong>and you can reply straight from Bakix</strong>. You will never be the last to know.",
    "Nepřečtené první": "Unread first",
    "Push okamžitě": "Instant push",
    "doručené": "inbox",
    "2 nové": "2 new",
    "Změna termínu písemky z matematiky — přesouvá se na čtvrtek…":
        "The maths test has been rescheduled — moved to Thursday…",
    "před 9 min": "9 min ago",
    "Zítra nezapomeňte čtenářské deníky, vybírám na začátku hodiny…":
        "Do not forget your reading journals tomorrow, I will collect them at the start of class…",
    "před 2 h": "2 h ago",
    "V pátek končí výuka v 11:40 z důvodu pedagogické rady…":
        "Classes end at 11:40 on Friday due to a staff meeting…",
    "včera": "yesterday",

    # ── welcome: AI section ─────────────────────────────────────────
    "AI, co zná": "AI that knows",
    "Není to jen chatbot. Bakix AI vidí tvůj rozvrh, úkoly i průměry — takže radí přesně tobě. <strong>Vysvětlí látku, vytvoří studijní plán, vygeneruje procvičování.</strong>":
        "Not just another chatbot. Bakix AI sees your timetable, homework and averages — so its advice fits you. <strong>It explains topics, builds study plans, generates practice.</strong>",
    "Gemini uvnitř": "Gemini inside",
    "Studijní stránky na míru": "Custom study pages",
    "Vlastní AI persony": "Custom AI personas",
    "AI chat": "AI chat",
    "Vysvětli mi Pythagorovu větu": "Explain the Pythagorean theorem",
    "odpověď za 1,2 s": "answered in 1.2 s",
    "zná tvůj rozvrh i známky": "knows your timetable and grades",
    "Jasně! V pravoúhlém trojúhelníku platí a² + b² = c². Přepona na druhou = součet čtverců odvěsen. Zítra ji píšeš — chceš tři příklady na procvičení?":
        "Sure! In a right triangle, a² + b² = c². The hypotenuse squared equals the sum of the squares of the legs. Your test is tomorrow — want three practice problems?",

    # ── welcome: stats / CTA / footer ───────────────────────────────
    "a víš všechno o dnešku": "and you know everything about today",
    "aktivních studentů": "active students",
    "podporovaných škol": "supported schools",
    "na start. fakt.": "to start. really.",
    "Přestaň lovit známky.": "Stop hunting for grades.",
    "Najdeš svoji školu, přihlásíš se <strong>Bakaláři účtem</strong> — a hotovo. Žádná registrace, žádné heslo navíc.":
        "Find your school, sign in <strong>with your Bakaláři account</strong> — done. No registration, no extra password.",
    "Spustit Bakix": "Launch Bakix",
    "PWA · žádná instalace ze storu · zdarma": "PWA · no app store install · free",
    "Právní informace": "Legal",
    "Bakix není oficiální produkt Bakaláři software a.s.": "Bakix is not an official product of Bakaláři software a.s.",

    # ── fuzzy fixes (set explicit translations, clear flag) ─────────
    "Bakix pracuje stejně jako aplikace Bakaláři — získá dočasný token platný 8 hodin. Po odhlášení ho ihned smaže.":
        "Bakix works just like the Bakaláři app — it obtains a temporary token valid for 8 hours. It is deleted immediately when you log out.",
    "Data jdou přímo ze školy": "Data comes straight from your school",
    "Bakix volá přímo server vaší školy — žádná třetí strana vaše data nevidí.":
        "Bakix talks directly to your school's server — no third party ever sees your data.",
    "Výsledky hledání": "Search results",
    "Přehledný AI dashboard pro Bakaláře. Známky, rozvrh a suplování — přístupné, krásné, chytré. Zdarma.":
        "A clean AI dashboard for Bakaláři. Grades, timetable and substitutions — accessible, beautiful, smart. Free.",
    "Začít": "Start",
    "Škola.": "School.",
    "přehledně.": "clear.",
    "Vítej zpět,": "Welcome back,",
    "Push notifikace": "Push notifications",
    "Nová známka": "New grade",
    "Všechny známky.": "All your grades.",
    "Přehled známek": "Grade overview",
    "známek": "grades",
    "Září": "September",
    "2 změny": "2 changes",
    "Psaní zpráv": "Compose messages",
    "Ředitelství školy": "School administration",
    "tvoje známky.": "your grades.",
    "Začni je mít.": "Start owning them.",
    "vyrobeno studenty pro studenty": "made by students for students",
}

with open(PO, "rb") as f:
    cat = read_po(f)

filled, unfuzzied, missing = 0, 0, []
for msg in cat:
    if not msg.id:
        continue
    mid = str(msg.id)
    if mid in T:
        if not str(msg.string).strip():
            filled += 1
        msg.string = T[mid]
        if msg.fuzzy:
            msg.flags.discard("fuzzy")
            unfuzzied += 1
    elif not str(msg.string).strip() or msg.fuzzy:
        missing.append(mid)

buf = io.BytesIO()
write_po(buf, cat, width=0, sort_output=False, ignore_obsolete=False)
with open(PO, "wb") as f:
    f.write(buf.getvalue())

print("filled:", filled, "| unfuzzied:", unfuzzied, "| still missing:", len(missing))
for m in missing:
    print("  MISSING:", m.encode("unicode_escape").decode())
