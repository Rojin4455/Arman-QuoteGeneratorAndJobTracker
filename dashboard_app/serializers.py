from rest_framework import serializers
from .models import Invoice, InvoiceItem


class InvoiceItemSerializer(serializers.ModelSerializer):
    """Serializer for InvoiceItem model"""
    
    total_amount = serializers.SerializerMethodField()
    
    class Meta:
        model = InvoiceItem
        fields = [
            'id', 'item_id', 'product_id', 'name', 'description',
            'currency', 'qty', 'amount', 'total_amount',
            'tax_inclusive', 'taxes', 'created_at'
        ]
    
    def get_total_amount(self, obj):
        """Calculate total amount including taxes"""
        total = float(obj.qty) * float(obj.amount)
        if not obj.tax_inclusive and obj.taxes:
            for tax in obj.taxes:
                tax_rate = tax.get('rate', 0)
                total += total * (tax_rate / 100)
        return round(total, 2)


class InvoiceSerializer(serializers.ModelSerializer):
    """Basic serializer for Invoice model (list view)"""
    
    is_overdue = serializers.BooleanField(read_only=True)
    items_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Invoice
        fields = [
            'id', 'invoice_id', 'invoice_number', 'name', 'status',
            'contact_id', 'contact_name', 'contact_email', 'contact_phone',
            'currency', 'currency_symbol', 'total', 'amount_paid', 'amount_due',
            'issue_date', 'due_date', 'created_at', 'updated_at',
            'is_overdue', 'items_count', 'location_id'
        ]
    
    def get_items_count(self, obj):
        """Get count of invoice items"""
        return obj.items.count()


class InvoiceDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for Invoice model (detail view)"""
    
    items = InvoiceItemSerializer(many=True, read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = Invoice
        fields = '__all__'