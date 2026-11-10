# Playbook: rework after a failed acceptance test

A person tested the build and rejected it. Their feedback is in the package.

1. Reproduce what the tester saw, on the commit they tested.
2. Decide which it is: the code does not match the approved spec (fix the code), or the approved spec
   does not match what the tester wants (stop and report; that is a design change and needs re-approval).
3. Fix it and add an automated test for the exact case the tester found, named with the criterion id.
4. Push to the named branch. CI and acceptance testing run again on the new commit.
