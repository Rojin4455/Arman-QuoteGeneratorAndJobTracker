"""
Public contact profile address APIs.

Addresses are managed in-app (not synced from GHL custom fields).
Scoped by location_id + GHL contact_id.
"""
from uuid import uuid4

from django.db.models import Max
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.contact_profile_serializers import (
    ContactAddressSerializer,
    ContactAddressWriteSerializer,
)
from accounts.models import Address, Contact
from accounts.permissions import AccountScopedPermission


def _resolve_contact(account, ghl_contact_id: str) -> Contact:
    return get_object_or_404(
        Contact,
        contact_id=ghl_contact_id,
        account=account,
    )


class ContactAddressListCreateView(APIView):
    permission_classes = [AccountScopedPermission, AllowAny]

    def get(self, request, ghl_contact_id):
        account = request.account
        contact = _resolve_contact(account, ghl_contact_id)
        addresses = Address.objects.filter(contact=contact).order_by('order', 'id')
        return Response(ContactAddressSerializer(addresses, many=True).data)

    def post(self, request, ghl_contact_id):
        account = request.account
        contact = _resolve_contact(account, ghl_contact_id)
        serializer = ContactAddressWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        next_order = (
            Address.objects.filter(contact=contact).aggregate(max_order=Max('order'))['max_order']
            or 0
        ) + 1
        address_fields = dict(serializer.validated_data)
        address_fields['name'] = (address_fields.get('name') or '').strip() or f'Property {next_order}'

        address = Address.objects.create(
            contact=contact,
            address_id=f'app_{uuid4().hex[:16]}',
            order=next_order,
            **address_fields,
        )
        return Response(ContactAddressSerializer(address).data, status=status.HTTP_201_CREATED)


class ContactAddressDetailView(APIView):
    permission_classes = [AccountScopedPermission, AllowAny]

    def _get_address(self, account, ghl_contact_id, pk):
        contact = _resolve_contact(account, ghl_contact_id)
        return get_object_or_404(Address, pk=pk, contact=contact)

    def patch(self, request, ghl_contact_id, pk):
        address = self._get_address(request.account, ghl_contact_id, pk)
        serializer = ContactAddressWriteSerializer(address, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ContactAddressSerializer(address).data)

    def delete(self, request, ghl_contact_id, pk):
        address = self._get_address(request.account, ghl_contact_id, pk)
        address.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
