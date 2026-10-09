# FSC volume report

Generated 2026-10-09 from the database (real DIBBS data only; synthetic SPEZZ* demo rows excluded).

Ranked by number of solicitations. `*` marks the current default watchlist.

| FSC | Solicitations | Automated (T/U) | Median award value | NSNs with >= 3 awards | Distinct awardees | NSNs | Award rows |
|---|---|---|---|---|---|---|---|
| 6145 * | 58 | 100% | $372 | 0% | 2 | 55 | 2 |

## Observed in the captured index files

From the five daily RFQ index files captured during data discovery (see docs/data_sources.md):

```json
{
  "5305": {
    "lines": 304,
    "solicitations": 265,
    "lines_by_type": {
      "T": 299,
      "Q": 1,
      "U": 4
    },
    "solicitations_by_type": {
      "T": 260,
      "U": 4,
      "Q": 1
    },
    "set_aside": {
      "N": 283,
      "Y": 15,
      "R": 3,
      "H": 2,
      "L": 1
    }
  },
  "5306": {
    "lines": 179,
    "solicitations": 177,
    "lines_by_type": {
      "T": 174,
      "Q": 2,
      "U": 3
    },
    "solicitations_by_type": {
      "T": 172,
      "U": 3,
      "Q": 2
    },
    "set_aside": {
      "N": 155,
      "Y": 21,
      "H": 1,
      "R": 2
    }
  },
  "5310": {
    "lines": 275,
    "solicitations": 246,
    "lines_by_type": {
      "T": 272,
      "U": 3
    },
    "solicitations_by_type": {
      "T": 243,
      "U": 3
    },
    "set_aside": {
      "N": 249,
      "Y": 22,
      "H": 2,
      "R": 2
    }
  },
  "5330": {
    "lines": 384,
    "solicitations": 372,
    "lines_by_type": {
      "T": 325,
      "U": 49,
      "Q": 10
    },
    "solicitations_by_type": {
      "T": 313,
      "U": 49,
      "Q": 10
    },
    "set_aside": {
      "N": 330,
      "Y": 50,
      "L": 3,
      "R": 1
    }
  },
  "5340": {
    "lines": 482,
    "solicitations": 474,
    "lines_by_type": {
      "T": 444,
      "U": 28,
      "Q": 10
    },
    "solicitations_by_type": {
      "T": 436,
      "U": 28,
      "Q": 10
    },
    "set_aside": {
      "N": 440,
      "Y": 31,
      "L": 2,
      "R": 5,
      "H": 4
    }
  },
  "4730": {
    "lines": 407,
    "solicitations": 382,
    "lines_by_type": {
      "T": 398,
      "Q": 5,
      "U": 4
    },
    "solicitations_by_type": {
      "T": 373,
      "Q": 5,
      "U": 4
    },
    "set_aside": {
      "N": 366,
      "Y": 33,
      "R": 4,
      "H": 2,
      "L": 2
    }
  },
  "6145": {
    "lines": 107,
    "solicitations": 58,
    "lines_by_type": {
      "T": 105,
      "U": 2
    },
    "solicitations_by_type": {
      "T": 56,
      "U": 2
    },
    "set_aside": {
      "Y": 4,
      "N": 102,
      "L": 1
    }
  }
}
```

## How to read this

- **Automated share** high = more fast/auto awards decided on price: the pricing model matters most there.
- **NSNs with >= 3 awards** = how often the model has real NSN history (vs. cold start).
- **Distinct awardees** = competition; very few awardees with high incumbent share are hard to break into.
