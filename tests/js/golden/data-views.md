# Data views

Memory, plots, traces and keys.

## Reverse {#rev}

```memory step
head -> n1
n1: Node {val: 1, next -> n2}
n2: Node {val: 2, next -> null}
```
```memory step
head -> n2
n1: Node {val: 1, next -> null}
n2: Node {val: 2, next -> n1}
```

The list starts at [the head](#head). After [the reversal](#step-2), [the second node](#n2.next) points back.

## Descent {#gd}

```plot step x=-3..3
f(x) = x^2  "loss"
point p = (2.5, f(2.5))
```
```plot step x=-3..3
f(x) = x^2  "loss"
point p = (0.5, f(0.5))
```

The [loss](#f) is a bowl, and [one step](#step-2) moves [the point](#p) down it.

## The loop {#loop}

```python
def total(xs):
    t = 0
    for x in xs:
        t += x
    return t
```
```trace
L1  xs = [3, 5]
L2  t = 0
L3  x = 3
L4  t = 3
L3  x = 5
L4  t = 8
L5  return: 8
```

It starts [at zero](#trace-2), then [t becomes 8](#trace-6 "t = 8").

## Scripts {#scripts}

```json
{
  "name": "demo",
  "scripts": {"test": "vitest", "build": "vite build"}
}
```

The [test script](#scripts.test) runs the tests.
