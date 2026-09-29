from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models import Booking, Flight, User
from schemas import BookingOut, ErrorResponse, SeatClass
from services.pricing import compute_price


def _get_or_create_user(db: Session, user_id: int, name: str) -> User:
    """Return the user by id, creating a minimal record if they don't exist yet.

    Used by the internal hold-confirmation path so that travellers who were
    registered only in the Java hold service can still have a booking created
    in the Python backend without a name-mismatch rejection.
    """
    user = db.query(User).filter(User.user_id == user_id).first()
    if user:
        return user
    # Auto-provision a minimal user record so the booking can proceed.
    new_user = User(user_id=user_id, name=name, email=f"hold-traveler-{user_id}@galaxium.internal")
    db.add(new_user)
    db.flush()  # assign PK without committing yet
    return new_user


def book_flight(db: Session, user_id: int, name: str, flight_id: int, seat_class: SeatClass = 'economy') -> BookingOut | ErrorResponse:
    """Book a seat on a specific flight for a user in the specified seat class."""
    # Validate seat class
    valid_classes = ('economy', 'business', 'galaxium')
    if seat_class not in valid_classes:
        return ErrorResponse(
            error="Invalid seat class",
            error_code="INVALID_SEAT_CLASS",
            details=f"Seat class '{seat_class}' is not valid. Valid options are: economy, business, galaxium."
        )
    
    # Check flight exists
    flight = db.query(Flight).filter(Flight.flight_id == flight_id).first()
    if not flight:
        return ErrorResponse(
            error="Flight not found",
            error_code="FLIGHT_NOT_FOUND",
            details=f"The specified flight_id {flight_id} does not exist in our system. Please check the flight_id or use list_flights to see available flights."
        )

    # Check seats available for the specific class
    if seat_class == 'economy':
        seats_available = flight.economy_seats_available
    elif seat_class == 'business':
        seats_available = flight.business_seats_available
    else:  # galaxium
        seats_available = flight.galaxium_seats_available
    
    if seats_available < 1:
        return ErrorResponse(
            error=f"No {seat_class} seats available",
            error_code="NO_SEATS_AVAILABLE",
            details=f"The flight has no available seats in {seat_class} class. Please try a different class or check other flights."
        )

    # Check user exists and name matches
    user = db.query(User).filter(User.user_id == user_id, User.name == name).first()
    if not user:
        existing_user = db.query(User).filter(User.user_id == user_id).first()
        if existing_user:
            return ErrorResponse(
                error="Name mismatch",
                error_code="NAME_MISMATCH",
                details=f"User ID {user_id} exists but the name '{name}' does not match the registered name '{existing_user.name}'. Please verify the user's name or use the correct name for this user ID."
            )
        else:
            return ErrorResponse(
                error="User not found",
                error_code="USER_NOT_FOUND",
                details=f"User with ID {user_id} is not registered in our system. The user might need to register first, or you may need to check if the user_id is correct."
            )

    # Calculate dynamic price at time of booking
    seats_available = (
        flight.economy_seats_available if seat_class == 'economy'
        else flight.business_seats_available if seat_class == 'business'
        else flight.galaxium_seats_available
    )
    total_capacity = (
        flight.economy_capacity if seat_class == 'economy'
        else flight.business_capacity if seat_class == 'business'
        else flight.galaxium_capacity
    )
    price_paid = compute_price(
        base_price=flight.base_price,
        seat_class=seat_class,
        seats_available=seats_available,
        total_capacity=total_capacity,
        departure_time_str=flight.departure_time,
    )

    # Decrement the correct seat class counter
    if seat_class == 'economy':
        flight.economy_seats_available -= 1
    elif seat_class == 'business':
        flight.business_seats_available -= 1
    else:  # galaxium
        flight.galaxium_seats_available -= 1

    # Create booking
    new_booking = Booking(
        user_id=user_id,
        flight_id=flight_id,
        status="booked",
        booking_time=datetime.now(tz=timezone.utc).isoformat(),
        seat_class=seat_class,
        price_paid=price_paid
    )
    db.add(new_booking)
    db.commit()
    db.refresh(new_booking)
    return BookingOut.model_validate(new_booking)


def cancel_booking(db: Session, booking_id: int) -> BookingOut | ErrorResponse:
    """Cancel an existing booking by its booking_id and restore seat to correct class."""
    booking = db.query(Booking).filter(Booking.booking_id == booking_id).first()
    if not booking:
        return ErrorResponse(
            error="Booking not found",
            error_code="BOOKING_NOT_FOUND",
            details=f"Booking with ID {booking_id} not found. The booking may have been deleted or the booking_id may be incorrect. Please verify the booking_id or check if the booking exists."
        )

    if booking.status == "cancelled":
        return ErrorResponse(
            error="Booking already cancelled",
            error_code="ALREADY_CANCELLED",
            details=f"Booking {booking_id} is already cancelled and cannot be cancelled again. The booking status is currently '{booking.status}'. If you need to make changes, please contact support."
        )

    # Restore seat to the correct class
    flight = db.query(Flight).filter(Flight.flight_id == booking.flight_id).first()
    if flight:
        if booking.seat_class == 'economy':
            flight.economy_seats_available += 1
        elif booking.seat_class == 'business':
            flight.business_seats_available += 1
        elif booking.seat_class == 'galaxium':
            flight.galaxium_seats_available += 1

    booking.status = "cancelled"
    db.commit()
    db.refresh(booking)
    return BookingOut.model_validate(booking)


def get_bookings(db: Session, user_id: int) -> list[BookingOut]:
    """Retrieve all bookings for a specific user."""
    bookings = db.query(Booking).filter(Booking.user_id == user_id).all()
    return [BookingOut.model_validate(b) for b in bookings]


def book_flight_from_hold(db: Session, user_id: int, name: str, flight_id: int, seat_class: SeatClass = 'economy') -> BookingOut | ErrorResponse:
    """Trusted internal booking path used by the Java hold-confirmation endpoint.

    Differences from book_flight:
    - Skips the strict user name-match check — the hold service is trusted to
      have validated the traveller identity at quote creation time.
    - Auto-provisions a minimal User record if the traveller does not yet exist
      in the Python backend's users table.
    """
    valid_classes = ('economy', 'business', 'galaxium')
    if seat_class not in valid_classes:
        return ErrorResponse(
            error="Invalid seat class",
            error_code="INVALID_SEAT_CLASS",
            details=f"Seat class '{seat_class}' is not valid. Valid options are: economy, business, galaxium."
        )

    flight = db.query(Flight).filter(Flight.flight_id == flight_id).first()
    if not flight:
        return ErrorResponse(
            error="Flight not found",
            error_code="FLIGHT_NOT_FOUND",
            details=f"The specified flight_id {flight_id} does not exist in our system."
        )

    if seat_class == 'economy':
        seats_available = flight.economy_seats_available
    elif seat_class == 'business':
        seats_available = flight.business_seats_available
    else:
        seats_available = flight.galaxium_seats_available

    if seats_available < 1:
        return ErrorResponse(
            error=f"No {seat_class} seats available",
            error_code="NO_SEATS_AVAILABLE",
            details=f"The flight has no available seats in {seat_class} class."
        )

    # Trusted internal path — get or create the user without name-match enforcement
    _get_or_create_user(db, user_id, name)

    total_capacity = (
        flight.economy_capacity if seat_class == 'economy'
        else flight.business_capacity if seat_class == 'business'
        else flight.galaxium_capacity
    )
    price_paid = compute_price(
        base_price=flight.base_price,
        seat_class=seat_class,
        seats_available=seats_available,
        total_capacity=total_capacity,
        departure_time_str=flight.departure_time,
    )

    if seat_class == 'economy':
        flight.economy_seats_available -= 1
    elif seat_class == 'business':
        flight.business_seats_available -= 1
    else:
        flight.galaxium_seats_available -= 1

    new_booking = Booking(
        user_id=user_id,
        flight_id=flight_id,
        status="booked",
        booking_time=datetime.now(tz=timezone.utc).isoformat(),
        seat_class=seat_class,
        price_paid=price_paid
    )
    db.add(new_booking)
    db.commit()
    db.refresh(new_booking)
    return BookingOut.model_validate(new_booking)
