from django import forms


class CartAddProductForm(forms.Form):
    quantity = forms.IntegerField(min_value=1, initial=1)
    override = forms.BooleanField(required=False, initial=False, widget=forms.HiddenInput)
    # Optional — populated only when adding a varianted product.
    # HiddenInput because the variant is chosen via a dropdown/radio
    # elsewhere on the page, not typed directly into this field.
    variant_id = forms.IntegerField(required=False, widget=forms.HiddenInput)