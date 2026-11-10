# Playbook: repair a failed gate

The package contains immutable failing evidence. That evidence is the whole scope of this task.

1. Reproduce the failure locally first. If you cannot reproduce it, stop and report what you tried.
2. Find the cause. Fix the cause, not the symptom: never delete, skip, loosen or mock away a failing
   check, and never raise a timeout or threshold to get a pass.
3. Add or tighten a test that would have caught this, named with the criterion id it protects.
4. Run the full suite, then push to the named branch. A new commit resets every gate, by design.
