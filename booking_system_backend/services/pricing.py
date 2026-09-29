"""Dynamic pricing engine for Galaxium Travels flights.

Price is computed from a base price plus two independent factors:

1. **Per-booking surge** — each seat booked in a class increases the price by
   ×1.15 for the next booking.
   price = base × class_mult × advance_mult × 1.15^seats_booked

2. **Advance booking** — closer to departure, the more expensive.
   - > 30 days away  → 0.90× (10 % early-bird discount)
   - 14 – 30 days    → 1.00× (standard)
   - 7 – 14 days     → 1.20× (+20 %)
   - < 7 days        → 1.40× (+40 %)

All prices are returned as integers (rounded to nearest whole unit).
"""

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Base class multipliers (economy is baseline 1×)
# ---------------------------------------------------------------------------
CLASS_MULTIPLIERS: dict[str, float] = {
    'economy': 1.0,
    'business': 2.5,
    'galaxium': 5.0,
}


# ---------------------------------------------------------------------------
# Per-booking surge factor
# ---------------------------------------------------------------------------
def _booking_surge_multiplier(seats_available: int, total_capacity: int) -> float:
    """Return a price multiplier of 1.15^seats_booked.

    seats_booked = total_capacity - seats_available
    Each seat sold raises the price by 15% for the next booking.
    """
    if total_capacity <= 0:
        return 1.0
    seats_booked = max(total_capacity - seats_available, 0)
    return 1.15 ** seats_booked


# ---------------------------------------------------------------------------
# Advance-booking factor
# ---------------------------------------------------------------------------
def _advance_multiplier(departure_time_str: str) -> float:
    """Return a price multiplier based on days until departure.

    Accepts departure_time in either 'YYYY-MM-DD HH:MM' or ISO-8601 format.
    Falls back to 1.0 (no adjustment) if the string cannot be parsed.
    """
    formats = ["%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"]
    departure: datetime | None = None
    for fmt in formats:
        try:
            departure = datetime.strptime(departure_time_str, fmt)  # noqa: DTZ007
            break
        except ValueError:
            continue

    if departure is None:
        return 1.0  # cannot parse → no adjustment

    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)  # naive UTC for comparison
    days_ahead = (departure - now).total_seconds() / 86_400

    if days_ahead > 30:
        return 0.90   # early-bird discount
    elif days_ahead > 14:
        return 1.00   # standard
    elif days_ahead > 7:
        return 1.20   # near-term surcharge
    else:
        return 1.40   # last-minute surcharge


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def compute_price(
    base_price: int,
    seat_class: str,
    seats_available: int,
    total_capacity: int,
    departure_time_str: str,
) -> int:
    """Compute the dynamic price for a single seat class.

    Args:
        base_price:         The flight's stored economy base price.
        seat_class:         One of 'economy', 'business', 'galaxium'.
        seats_available:    Current remaining seats in this class.
        total_capacity:     Original total seats for this class (used for the
                            demand ratio).  Pass the *current* available count
                            as a proxy when the original capacity is not stored
                            — the engine still works; it will just not surge
                            until nearly sold out.
        departure_time_str: The flight's departure_time string.

    Returns:
        Price as an integer (rounded).
    """
    class_mult = CLASS_MULTIPLIERS.get(seat_class, 1.0)
    surge_mult = _booking_surge_multiplier(seats_available, total_capacity)
    advance_mult = _advance_multiplier(departure_time_str)

    raw = base_price * class_mult * surge_mult * advance_mult
    return int(round(raw))


def compute_all_prices(
    base_price: int,
    economy_seats: int,
    business_seats: int,
    galaxium_seats: int,
    departure_time_str: str,
    *,
    # Original capacities — required to compute seats_booked = capacity - available.
    # If omitted, seats_booked is treated as 0 (no surge applied).
    economy_capacity: int | None = None,
    business_capacity: int | None = None,
    galaxium_capacity: int | None = None,
) -> dict[str, int]:
    """Return a dict with economy_price, business_price, galaxium_price."""
    return {
        'economy_price': compute_price(
            base_price, 'economy',
            economy_seats, economy_capacity or max(economy_seats, 1),
            departure_time_str,
        ),
        'business_price': compute_price(
            base_price, 'business',
            business_seats, business_capacity or max(business_seats, 1),
            departure_time_str,
        ),
        'galaxium_price': compute_price(
            base_price, 'galaxium',
            galaxium_seats, galaxium_capacity or max(galaxium_seats, 1),
            departure_time_str,
        ),
    }

# Made with Bob
