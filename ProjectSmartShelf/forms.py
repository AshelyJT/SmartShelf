from django import forms
from django.contrib.auth.models import User


class PricePredictionForm(forms.Form):
    CATEGORY_CHOICES = [('Dairy', 'Dairy'), ('Meat', 'Meat'), ('Produce', 'Produce')] 
    
    product_name = forms.CharField(label='Product Name', max_length=100)
    category = forms.ChoiceField(choices=CATEGORY_CHOICES)
    inventory_level = forms.IntegerField(label='Current Inventory')
    days_until_expiry = forms.IntegerField(label='Days Until Expiry')
    units_sold = forms.IntegerField(label='Avg Units Sold')

