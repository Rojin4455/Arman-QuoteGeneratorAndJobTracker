from rest_framework import serializers

from accounts.models import Address


class ContactAddressSerializer(serializers.ModelSerializer):
    full_address = serializers.SerializerMethodField()

    class Meta:
        model = Address
        fields = [
            'id',
            'address_id',
            'name',
            'order',
            'street_address',
            'city',
            'state',
            'postal_code',
            'gate_code',
            'number_of_floors',
            'property_sqft',
            'property_type',
            'full_address',
        ]
        read_only_fields = ['id', 'address_id', 'order', 'full_address']

    def get_full_address(self, obj):
        return obj.get_full_address()


class ContactAddressWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = [
            'name',
            'street_address',
            'city',
            'state',
            'postal_code',
            'gate_code',
            'number_of_floors',
            'property_sqft',
            'property_type',
        ]

    def validate(self, attrs):
        street = (attrs.get('street_address') or '').strip()
        city = (attrs.get('city') or '').strip()
        state = (attrs.get('state') or '').strip()
        postal = (attrs.get('postal_code') or '').strip()
        if not any([street, city, state, postal]):
            raise serializers.ValidationError(
                'Provide at least one address line (street, city, state, or postal code).'
            )
        return attrs
