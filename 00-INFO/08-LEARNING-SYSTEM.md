# 08 — Learning System

This project can use feedback from previous results to improve future recommendations.

## In simple terms

```text
video result
    ↓
engagement / feedback
    ↓
learning data
    ↓
model/recommendation update
    ↓
next production plan
```

The learning code lives primarily in the application package and works with the project's knowledge and recommendation components.

## What it is not

It is not a magic self-training system that automatically makes every video better. It needs useful feedback/history before learned recommendations become meaningful.

## Why there are several learning files

The learning system is split into smaller pieces so that data handling, learning, and recommendation logic can evolve independently.

See [05 — Project Map](05-PROJECT-MAP.md) for the relevant modules.

## Safe expectations

Treat learned recommendations as suggestions rather than guarantees. Keep the underlying feedback/data available and inspect results when changing the learning behavior.
