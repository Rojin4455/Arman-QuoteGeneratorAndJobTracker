# Appointment API Documentation

## Base URL
All appointment endpoints are under: `/api/jobtracker/appointments/`

## Authentication
All endpoints require authentication. Use `IsAuthenticatedOrReadOnly` permission:
- **Admins**: Full access to all appointments
- **Normal Users**: Can only access appointments assigned to them or where they are in the users list

---

## Status Choices

The following appointment statuses are available:

| Status Value | Display Name | Description |
|-------------|--------------|-------------|
| `new` | New/Unconfirmed | New appointment, not yet confirmed |
| `confirmed` | Confirmed | Appointment has been confirmed |
| `cancelled` | Cancelled | Appointment has been cancelled |
| `showed` | Showed | Customer showed up for appointment |
| `noshow` | No Show | Customer did not show up |
| `invalid` | Invalid | Invalid appointment |

---

## Endpoints

### 1. List All Appointments
**GET** `/api/jobtracker/appointments/`

Returns a list of appointments based on user permissions.

**Query Parameters:**
- `page` (optional): Page number for pagination
- `page_size` (optional): Number of items per page (default: 20, max: 100)

**Response:** `200 OK`
```json
[
  {
    "appointment_id": "550e8400-e29b-41d4-a716-446655440000",
    "ghl_appointment_id": "zGG8PZT41ZXuni23UY8k_1765371600000_3600",
    "location_id": "b8qvo7VooP3JD3dIZU42",
    "title": "Antiquite's Monthly Window Cleaning",
    "address": "https://meet.example.com/room123",
    "calendar_id": "xAVQMzqPGOWR2kDN8B2P",
    "appointment_status": "confirmed",
    "source": "calendar",
    "notes": "Quarterly Gutter Cleaning $224.00",
    "start_time": "2025-12-10T07:00:00-06:00",
    "end_time": "2025-12-10T08:00:00-06:00",
    "date_added": "2025-09-05T18:43:20.755Z",
    "date_updated": "2025-11-18T16:29:52.912Z",
    "ghl_contact_id": "u7KLgLmk0wfHgd30o5pR",
    "group_id": "TqDBfQSv31iOBAk0xnXk",
    "assigned_user_id": "7jxoKigWEDwBWAUaL1wJ",
    "assigned_user_name": "John Doe",
    "assigned_user_email": "john@example.com",
    "ghl_assigned_user_id": "7jxoKigWEDwBWAUaL1wJ",
    "contact_id": "u7KLgLmk0wfHgd30o5pR",
    "contact_name": "Jane Smith",
    "contact_email": "jane@example.com",
    "users": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440001",
        "email": "user1@example.com",
        "name": "User One",
        "ghl_user_id": "7jxoKigWEDwBWAUaL1wJ"
      }
    ],
    "users_ghl_ids": ["7jxoKigWEDwBWAUaL1wJ"],
    "created_at": "2025-09-05T18:43:20.755Z",
    "updated_at": "2025-11-18T16:29:52.912Z"
  }
]
```

---

### 2. Retrieve Single Appointment
**GET** `/api/jobtracker/appointments/{appointment_id}/`

Returns details of a specific appointment.

**Path Parameters:**
- `appointment_id` (UUID): The UUID of the appointment

**Response:** `200 OK`
```json
{
  "appointment_id": "550e8400-e29b-41d4-a716-446655440000",
  "ghl_appointment_id": "zGG8PZT41ZXuni23UY8k_1765371600000_3600",
  "location_id": "b8qvo7VooP3JD3dIZU42",
  "title": "Antiquite's Monthly Window Cleaning",
  "address": "https://meet.example.com/room123",
  "calendar_id": "xAVQMzqPGOWR2kDN8B2P",
  "appointment_status": "confirmed",
  "source": "calendar",
  "notes": "Quarterly Gutter Cleaning $224.00",
  "start_time": "2025-12-10T07:00:00-06:00",
  "end_time": "2025-12-10T08:00:00-06:00",
  "date_added": "2025-09-05T18:43:20.755Z",
  "date_updated": "2025-11-18T16:29:52.912Z",
  "ghl_contact_id": "u7KLgLmk0wfHgd30o5pR",
  "group_id": "TqDBfQSv31iOBAk0xnXk",
  "assigned_user_id": "7jxoKigWEDwBWAUaL1wJ",
  "assigned_user_name": "John Doe",
  "assigned_user_email": "john@example.com",
  "ghl_assigned_user_id": "7jxoKigWEDwBWAUaL1wJ",
  "contact_id": "u7KLgLmk0wfHgd30o5pR",
  "contact_name": "Jane Smith",
  "contact_email": "jane@example.com",
  "users": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440001",
      "email": "user1@example.com",
      "name": "User One",
      "ghl_user_id": "7jxoKigWEDwBWAUaL1wJ"
    }
  ],
  "users_ghl_ids": ["7jxoKigWEDwBWAUaL1wJ"],
  "created_at": "2025-09-05T18:43:20.755Z",
  "updated_at": "2025-11-18T16:29:52.912Z"
}
```

**Error Responses:**
- `404 Not Found`: Appointment not found
- `403 Forbidden`: User doesn't have permission to access this appointment

---

### 3. Create Appointment
**POST** `/api/jobtracker/appointments/`

Creates a new appointment.

**Request Body:**
```json
{
  "title": "New Appointment",
  "address": "https://meet.example.com/room123",
  "calendar_id": "xAVQMzqPGOWR2kDN8B2P",
  "appointment_status": "new",
  "source": "manual",
  "notes": "Customer requested this appointment",
  "start_time": "2025-12-15T10:00:00-06:00",
  "end_time": "2025-12-15T11:00:00-06:00",
  "location_id": "b8qvo7VooP3JD3dIZU42",
  "ghl_contact_id": "u7KLgLmk0wfHgd30o5pR",
  "group_id": "TqDBfQSv31iOBAk0xnXk",
  "assigned_user_uuid": "550e8400-e29b-41d4-a716-446655440001",
  "users_list": [
    "550e8400-e29b-41d4-a716-446655440002",
    "550e8400-e29b-41d4-a716-446655440003"
  ]
}
```

**Field Descriptions:**
- `title` (string, required): Appointment title
- `address` (string, optional): Meeting URL or address
- `calendar_id` (string, optional): Calendar ID
- `appointment_status` (string, optional): One of: `new`, `confirmed`, `cancelled`, `showed`, `noshow`, `invalid`
- `source` (string, optional): Source of appointment (e.g., "calendar", "manual")
- `notes` (string, optional): Appointment notes
- `start_time` (datetime, required): ISO 8601 format datetime
- `end_time` (datetime, required): ISO 8601 format datetime (must be after start_time)
- `location_id` (string, optional): Location ID (auto-filled from credentials if not provided)
- `ghl_contact_id` (string, optional): GHL contact ID
- `group_id` (string, optional): Group ID
- `assigned_user_uuid` (UUID, optional): UUID of the user to assign as the primary assigned user. Users can view appointments they're assigned to.
- `ghl_assigned_user_id` (string, optional): GHL assigned user ID (alternative to assigned_user_uuid)
- `users_list` (array of UUIDs, optional): List of user UUIDs to assign to this appointment. Users in this list can view the appointment.

**Response:** `201 Created`
```json
{
  "appointment_id": "550e8400-e29b-41d4-a716-446655440000",
  "ghl_appointment_id": "local_550e8400-e29b-41d4-a716-446655440000",
  "location_id": "b8qvo7VooP3JD3dIZU42",
  "title": "New Appointment",
  "address": "https://meet.example.com/room123",
  "calendar_id": "xAVQMzqPGOWR2kDN8B2P",
  "appointment_status": "new",
  "source": "manual",
  "notes": "Customer requested this appointment",
  "start_time": "2025-12-15T10:00:00-06:00",
  "end_time": "2025-12-15T11:00:00-06:00",
  "date_added": null,
  "date_updated": null,
  "ghl_contact_id": "u7KLgLmk0wfHgd30o5pR",
  "group_id": "TqDBfQSv31iOBAk0xnXk",
  "assigned_user_id": "7jxoKigWEDwBWAUaL1wJ",
  "assigned_user_name": "John Doe",
  "assigned_user_email": "john@example.com",
  "ghl_assigned_user_id": "7jxoKigWEDwBWAUaL1wJ",
  "contact_id": "u7KLgLmk0wfHgd30o5pR",
  "contact_name": "Jane Smith",
  "contact_email": "jane@example.com",
  "users": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440001",
      "email": "user1@example.com",
      "name": "User One",
      "ghl_user_id": "7jxoKigWEDwBWAUaL1wJ"
    }
  ],
  "users_ghl_ids": [],
  "created_at": "2025-12-10T10:00:00Z",
  "updated_at": "2025-12-10T10:00:00Z"
}
```

**Error Responses:**
- `400 Bad Request`: Validation errors
  ```json
  {
    "appointment_status": ["Invalid status. Must be one of: new, confirmed, cancelled, showed, noshow, invalid"],
    "end_time": ["End time must be after start time"]
  }
  ```

---

### 4. Update Appointment (Full Update)
**PUT** `/api/jobtracker/appointments/{appointment_id}/`

Performs a full update of an appointment (all fields must be provided).

**Path Parameters:**
- `appointment_id` (UUID): The UUID of the appointment

**Request Body:** (Same as Create, all fields required)

**Response:** `200 OK` (Same structure as Create response)

**Error Responses:**
- `404 Not Found`: Appointment not found
- `403 Forbidden`: User doesn't have permission to update this appointment
- `400 Bad Request`: Validation errors

---

### 5. Partial Update Appointment
**PATCH** `/api/jobtracker/appointments/{appointment_id}/`

Performs a partial update of an appointment (only provided fields are updated).

**Path Parameters:**
- `appointment_id` (UUID): The UUID of the appointment

**Request Body:** (Any subset of fields from Create)
```json
{
  "appointment_status": "confirmed",
  "notes": "Updated notes",
  "assigned_user_uuid": "550e8400-e29b-41d4-a716-446655440001",
  "users_list": [
    "550e8400-e29b-41d4-a716-446655440002"
  ]
}
```

**Response:** `200 OK` (Same structure as Create response)

**Error Responses:**
- `404 Not Found`: Appointment not found
- `403 Forbidden`: User doesn't have permission to update this appointment
- `400 Bad Request`: Validation errors

---

### 6. Delete Appointment
**DELETE** `/api/jobtracker/appointments/{appointment_id}/`

Deletes an appointment.

**Path Parameters:**
- `appointment_id` (UUID): The UUID of the appointment

**Response:** `204 No Content`

**Error Responses:**
- `404 Not Found`: Appointment not found
- `403 Forbidden`: User doesn't have permission to delete this appointment

---

## Calendar View Endpoint

### Get Appointments for Calendar View
**GET** `/api/jobtracker/appointments-calendar/`

Returns appointments in a date range for calendar display.

**Query Parameters:**
- `start` (required): ISO 8601 datetime string - start of date range
- `end` (required): ISO 8601 datetime string - end of date range
- `status` (optional): Comma-separated list of statuses (e.g., `confirmed,cancelled`)
- `assigned_user_ids` (optional): Comma-separated list of user UUIDs or emails
- `search` (optional): Search in title and notes

**Example:**
```
GET /api/jobtracker/appointments-calendar/?start=2025-12-01T00:00:00Z&end=2025-12-31T23:59:59Z&status=confirmed,new
```

**Response:** `200 OK`
```json
[
  {
    "appointment_id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "Antiquite's Monthly Window Cleaning",
    "start_time": "2025-12-10T07:00:00-06:00",
    "end_time": "2025-12-10T08:00:00-06:00",
    "appointment_status": "confirmed",
    "assigned_user_name": "John Doe",
    "contact_name": "Jane Smith",
    "address": "https://meet.example.com/room123",
    "notes": "Quarterly Gutter Cleaning $224.00",
    "source": "calendar",
    "users_count": 2
  }
]
```

---

## Example cURL Commands

### Create Appointment
```bash
curl -X POST "https://your-domain.com/api/jobtracker/appointments/" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "New Appointment",
    "appointment_status": "new",
    "start_time": "2025-12-15T10:00:00-06:00",
    "end_time": "2025-12-15T11:00:00-06:00",
    "assigned_user_uuid": "550e8400-e29b-41d4-a716-446655440001",
    "users_list": [
      "550e8400-e29b-41d4-a716-446655440002",
      "550e8400-e29b-41d4-a716-446655440003"
    ],
    "notes": "Customer requested this appointment"
  }'
```

### Update Appointment Status
```bash
curl -X PATCH "https://your-domain.com/api/jobtracker/appointments/550e8400-e29b-41d4-a716-446655440000/" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "appointment_status": "confirmed"
  }'
```

### Delete Appointment
```bash
curl -X DELETE "https://your-domain.com/api/jobtracker/appointments/550e8400-e29b-41d4-a716-446655440000/" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Get Appointment
```bash
curl -X GET "https://your-domain.com/api/jobtracker/appointments/550e8400-e29b-41d4-a716-446655440000/" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## Notes

1. **Auto-generated fields**: `ghl_appointment_id` is automatically generated for local appointments (format: `local_{uuid}`)
2. **Location ID**: If not provided during creation, it's automatically set from `GHLAuthCredentials`
3. **Permissions**: 
   - Admins can access all appointments
   - Normal users can only access appointments where they are assigned (`assigned_user`) or in the `users` list
4. **Date formats**: All datetime fields use ISO 8601 format (e.g., `2025-12-10T07:00:00-06:00`)
5. **User assignments**: 
   - Use `assigned_user_uuid` to set the primary assigned user (users can view appointments they're assigned to)
   - Use `users_list` field (array of user UUIDs) to assign multiple users to an appointment (users in this list can also view the appointment)
   - Both assigned users and users in the list have permission to view the appointment
6. **Read-only fields**: The following fields are read-only and cannot be modified via API:
   - `appointment_id`
   - `ghl_appointment_id`
   - `date_added`
   - `date_updated`
   - `created_at`
   - `updated_at`
   - `assigned_user_id`, `assigned_user_name`, `assigned_user_email` (derived from `assigned_user`)
   - `contact_id`, `contact_name`, `contact_email` (derived from `contact`)
   - `users` (read-only, use `users_list` to modify)
