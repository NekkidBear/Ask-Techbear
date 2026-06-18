"""
scripts/seed_presentation_versions.py — Seed display-optimized slideshow text
Gymnarctos Studios LLC

Creates PresentationVersion rows for each highlighted question.
Text is summarized and formatted for walk-by readability:
  - ~100-150 words max
  - Clear visual line breaks
  - Action steps on their own lines
  - TechBear voice and factual accuracy preserved
  - Any profanity that slipped through replaced with asterisks

Run with: python -m backend.scripts.seed_presentation_versions
Re-running is safe — skips existing entries unless --force is passed.
"""

import asyncio
import sys

from backend.database import get_db_context
from backend.models import PresentationVersion
from sqlalchemy import select

# ── Presentation texts keyed by question_id ────────────────────────────────
# Format conventions used here:
#   Blank lines  → paragraph breaks (whitespace-pre-wrap in the slideshow)
#   Bullet lines → start with •
#   STEP lines   → "STEP ONE:", "STEP TWO:", etc. for action sequences
#   ALL CAPS     → single emphatic word, per TechBear voice guide
# ──────────────────────────────────────────────────────────────────────────

PRESENTATION_VERSIONS = {
    # Clueless in Cincinnati — mysterious USB drive
    1: """\
Darling, that "lovely anonymous gift" is the digital equivalent of a ticking time bomb — and you were absolutely RIGHT to ask first. 🐻

That USB drive is a classic attack vector, honey.
Mysterious drives get your curiosity. Then they get your data. Then your WHOLE NETWORK.

• Do NOT plug it in. Not even a little peek.
• Have a forensics professional analyze it on an isolated machine.
• Until then, treat it like a stranger handing you candy from an unmarked van.

You've trained well, technocub.
Now let the experts finish the job. 🐾""",

    # Retro_Ryan — worst nightmare computer
    12: """\
Oh honey, you're asking me to unlock the encrypted vaults of my psychological trauma!

Picture it: a server closet running a dead dialect of binary.
Ventilation completely blocked — because the dust bunnies had built
an entire CIVILIZATION in the chassis. Held democratic elections.
Built infrastructure. Right next to the cooling fan.

The machine was running hot enough to slow-cook a prime rib.
Every diagnostic triggered a rogue smart toaster
firing back encrypted error messages in Morse code.

Twelve hours. One shot of espresso. A full hardware exorcism.

Respect your equipment, sugar —
bad airflow is the ultimate silent killer. 🐾""",

    # Mai — favorite repair job (was Fixer_Fiona)
    13: """\
Sugar, let me tell you about my crowning achievement.

A vintage workstation containing an entire country's agricultural ledger —
completely fried by an electrical surge.
The local team had given up. "The data is unrecoverable," they said.

I said: "Darling. Watch a professional work."

I went in with nothing but a hairpin and sheer willpower.
Manually bypassed the shorted logic gates.
Hand-soldered microscopic data lines back together.

When that drive spun back up and the green terminal light flickered to life —
the entire room erupted into applause. 🏆

Never let a lazy technician tell you a machine is past saving. 🐾""",

    # Glitch_Witch — haunted / possessed machines
    14: """\
Precious technocub — machines absolutely have souls.
I've looked deep into the silicone mainframe. I know things.

I once encountered a smart thermostat cross-wired with a vintage stereo.
It didn't just malfunction, honey — it staged a full domestic REBELLION.

Turn the temperature down?
It locked the doors, flashed the lights, and blasted disco music
through the security speakers at 3 o'clock in the morning.

I had to rewrite its firmware using a dial-up modem
just to convince it to stop terrorizing the kitchen.

Nine times out of ten, a "haunted" machine is a memory leak or corrupted logic loop.
But if your screen starts scrolling backward in binary —
sever that connection immediately and call us. 🐾""",

    # Tab_Collector — 80 browser tabs open
    15: """\
Darling, eighty open browser tabs is like trying to hold all your dirty laundry
while walking a tightrope over hungry alligators.

Your RAM is SCREAMING for mercy. 🚨

Every open tab is an active process eating your system memory.
Your CPU is working overtime keeping seventy-nine background scripts
from crashing into each other.

Your browser can multi-task — your hardware cannot.

Your homework: Install OneTab (free browser extension).
One click collapses all eighty monsters into a single text list,
freeing up 95% of your memory instantly.

Close the tabs. Free the processor.
Let your machine breathe, technocub. 🐾""",

    # Ahmed — ex still on streaming account (was Muted_Milo)
    20: """\
Honey, I am not sure whether to laugh, cry, or hurl a sequined server rack
right through a window.

Your ex is treating your streaming profile like a digital circus act —
and you're worried about "starting a fight"?

Sugar, THE FIGHT IS ALREADY IN YOUR HOUSE.

STEP ONE: Change the account password right now.
Not your anniversary. Not your dog's name. A REAL password.

STEP TWO: Find "Log Out of All Locations" in account settings.
Click it with authority.
His active session tokens are terminated. Game over.

Drag your digital boundaries out of the gutter, technocub.
The clown emoji stops TODAY. 🐾""",

    # Spilled_Coffee — tangled extension cords, sparking power strip
    21: """\
Lord have mercy, y'all — that description nearly blue-screened ME! 😱

A sparking power strip is not a minor glitch, honey.
That is a FIRE WAITING TO HAPPEN.

STEP ONE: Unplug that strip from the wall immediately.
Do not test it again first. Do not wait.

STEP TWO: Throw it away.
A sparking strip is dead hardware — scrap heap, not under your feet.

STEP THREE: Replace it with a heavy-duty surge protector
that has a built-in circuit breaker.

The dust bunnies under there have formed a civilization —
and you've been giving them fireworks.

Clean the cables. Manage the infrastructure.
Stop treating your setup like a campsite. 🐾""",

    # Ivan — clicked sketchy "speed up my PC" link, casino wallpaper (was Download_Debbie)
    22: """\
Darling, you've done the digital equivalent of signing up for a timeshare presentation —
with neon casino wallpaper as a party favor.

That "free speedup software" WAS the malware. The download was the trap.

Your emergency action plan:

STEP ONE: Disconnect from WiFi right now. Contain the damage.

STEP TWO: From a clean device, download Malwarebytes to a USB drive.

STEP THREE: Run the scanner. Repeat until clean.

STEP FOUR: Reconnect — then keep a real antivirus running going forward.
(Malwarebytes has a solid free tier. Use it.)

If things get weirder — disconnect immediately and call us.
We'll exorcise the ghosts from your machine. 🐾""",
}


async def seed(force: bool = False):
    async with get_db_context() as db:
        skipped = 0
        seeded = 0

        for question_id, display_text in PRESENTATION_VERSIONS.items():
            existing = await db.execute(
                select(PresentationVersion).where(
                    PresentationVersion.question_id == question_id
                )
            )
            row = existing.scalar_one_or_none()

            if row:
                if force:
                    row.display_text = display_text
                    print(f"  ↺  Updated presentation version for question {question_id}")
                    seeded += 1
                else:
                    print(f"  –  Skipping question {question_id} (already exists; use --force to overwrite)")
                    skipped += 1
            else:
                db.add(PresentationVersion(
                    question_id=question_id,
                    display_text=display_text,
                ))
                print(f"  ✓  Seeded presentation version for question {question_id}")
                seeded += 1

        await db.flush()
        print(f"\n✅ Done — {seeded} seeded/updated, {skipped} skipped.")


if __name__ == "__main__":
    force = "--force" in sys.argv
    asyncio.run(seed(force=force))