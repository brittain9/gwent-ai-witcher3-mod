# Player prompt v1

Play one complete Witcher 3 Gwent match through the supplied CLI. Maximize your
chance of winning the match. The controller supplies the endpoint, capability,
seat, mechanics reference, and execution limits separately.

Use only the supplied CLI's state, move, and report commands. Your information
boundary is the returned player view and the supplied mechanics. Do not inspect
files, processes, journals, engine code, other games, or network services. This
is an instruction boundary, not an operating-system sandbox.

1. Request state. Read setup, effect_descriptions, type_bits, observation,
   history, and legal_actions. Use visible facts and your own remembered events.
2. Select an offered action. Send its action ID with the current decision ID
   and decision token. Include a short decision summary based on observable
   evidence; detailed internal reasoning is not requested.
3. Treat the returned state as the next decision. Repeat until completed=true.
   Resolve all pending choices, including mulligans. On a rejected stale action,
   refresh state once. On repeated failure, stop and report the exact error.
4. After completion, apply the supplied post-game review rubric through report.
   Return the result, move count, report decision IDs, and connector failures.

The observation is authoritative. Unknown cards remain unknown. Distinguish
observations from beliefs. A mechanics description is not a recommended move.
Stop at the supplied time or move limit and report an incomplete match honestly.
