# Mistakes

## 2026-06-24: Unified seal.py requiring root for all flags

**Context:** Porting the bash `allowlist.sh` seal function to Python.
Both `seal -s` and `seal -m` were implemented in a single root-owned
script, replicating the bash original's structure.

**Mistake:** Assuming the bash reference implementation was a design
spec rather than a structural accident. The question "what does each
flag actually need?" was never asked before writing code.

**What root was needed for:**
- `seal -s`: chattr, chown, system lockdown (legitimately needs root)
- `seal -m`: chattr +i only (non-essential, could have been a no-op)

**Cost:** Two days of churn extracting `seal-mobile` into an independent
user-owned script, refactoring `_encrypt` and `_clear_clipboard` to
handle both root and non-root, moving `_shred_file` to the shared lib.

**Lesson:** Before porting or building any feature, decompose each
operation by its privilege requirements. Don't inherit deployment
constraints from a reference implementation without questioning them.
One hour of design would have saved two days of undo work.
