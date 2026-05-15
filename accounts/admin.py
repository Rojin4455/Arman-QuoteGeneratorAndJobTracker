from django.contrib import admin

from .models import GHLLocationIndex, GHLAuthCredentials, Address, Contact, Calendar, GHLCompanyAuth


@admin.register(GHLAuthCredentials)
class GHLAuthCredentialsAdmin(admin.ModelAdmin):
    list_display = ('user_id', 'location_id', 'company_name', 'company_id')
    search_fields = ('user_id', 'location_id', 'company_name', 'company_id')


# Register your models here.
admin.site.register(GHLLocationIndex)
admin.site.register(GHLCompanyAuth)
admin.site.register(Address)
admin.site.register(Contact)
admin.site.register(Calendar)