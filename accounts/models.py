# models.py
from django.db import models
from django.utils import timezone
import uuid
from django.contrib.postgres.fields import ArrayField, JSONField


class GHLAuthCredentials(models.Model):
    user_id = models.CharField(max_length=255, unique=True)
    access_token = models.TextField()
    refresh_token = models.TextField()
    expires_in = models.IntegerField()
    scope = models.CharField(max_length=500, null=True, blank=True)
    user_type = models.CharField(max_length=50, null=True, blank=True)
    company_id = models.CharField(max_length=255, null=True, blank=True)
    location_id = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user_id} - {self.company_id}"
    


class Contact(models.Model):
    contact_id = models.CharField(max_length=100, unique=True)
    first_name = models.CharField(max_length=100, blank=True, null=True)
    last_name = models.CharField(max_length=100, blank=True, null=True)
    phone = models.CharField(max_length=15, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    dnd = models.BooleanField(default=False)
    country = models.CharField(max_length=50, blank=True, null=True)
    date_added = models.DateTimeField(blank=True, null=True)
    tags = models.JSONField(default=list, blank=True)
    custom_fields = models.JSONField(default=list, blank=True)
    location_id = models.CharField(max_length=100)
    timestamp = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.email})"
    

class Webhook(models.Model):
    event = models.CharField(max_length=100)
    company_id = models.CharField(max_length=100)
    payload = models.JSONField()  # Store the entire raw payload
    received_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.event} - {self.company_id}"
    

class Address(models.Model):
    PROPERTY_TYPE_CHOICES = [
        ('residential', 'Residential'),
        ('commercial', 'Commercial'),
    ]
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='contact_location')
    address_id = models.CharField(max_length=500)
    name = models.CharField(max_length=100, blank=True, null=True, help_text="e.g. Home, Office, etc.")
    order = models.PositiveIntegerField(default=0, help_text="Order of this location for the contact")
    state = models.CharField(max_length=100, blank=True, null=True)
    street_address = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    postal_code = models.CharField(max_length=20, blank=True, null=True)
    gate_code = models.CharField(max_length=20, blank=True, null=True)
    number_of_floors = models.PositiveIntegerField(blank=True, null=True)
    property_sqft = models.PositiveIntegerField(blank=True, null=True)
    property_type = models.CharField(max_length=20, choices=PROPERTY_TYPE_CHOICES, blank=True, null=True)

    
    def get_full_address(self):
        """Returns a single string of the full address."""
        parts = [self.street_address, self.city, self.state, self.postal_code]
        # Filter out None or empty values and join with a comma and space
        return ', '.join(filter(None, parts))

    

    def __str__(self):
        return f"{self.street_address}, {self.city}, {self.state}"