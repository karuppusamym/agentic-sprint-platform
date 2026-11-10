# Playbook: integrate parallel work

1. Merge the task branches for this story into the integration branch in dependency order.
2. Resolve conflicts by re-reading the contract, never by choosing a side blindly.
3. Run contract, integration and end-to-end tests. Add integration tests for each criterion that
   crosses a module boundary, named with the criterion id.
4. Open one pull request for the story. Its head commit is the commit every later gate is pinned to.
