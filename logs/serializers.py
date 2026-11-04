from rest_framework import serializers

from .models import Pharmacy


# Mensagens em português para os erros que o DRF geraria em inglês.
MENSAGENS_OBRIGATORIO = {
    'required': 'Campo obrigatório.',
    'blank': 'Campo obrigatório.',
    'null': 'Campo obrigatório.',
}


class PharmacySerializer(serializers.ModelSerializer):
    """Cadastro de farmácia. O ``store_id`` é o identificador único."""

    # Declarados explicitamente para que a checagem de duplicidade e a de
    # acesso válido fiquem nos métodos ``validate_*`` abaixo, em português,
    # em vez das mensagens automáticas do DRF em inglês.
    store_id = serializers.CharField(max_length=15, error_messages=MENSAGENS_OBRIGATORIO)
    type_access = serializers.CharField(max_length=255, error_messages=MENSAGENS_OBRIGATORIO)
    pharmacy = serializers.CharField(max_length=255, error_messages=MENSAGENS_OBRIGATORIO)
    city = serializers.CharField(max_length=255, error_messages=MENSAGENS_OBRIGATORIO)
    system = serializers.CharField(max_length=255, error_messages=MENSAGENS_OBRIGATORIO)
    version = serializers.CharField(max_length=15, error_messages=MENSAGENS_OBRIGATORIO)

    class Meta:
        model = Pharmacy
        fields = [
            'store_id',
            'pharmacy',
            'city',
            'system',
            'type_access',
            'version',
            'created_at',
            'updated_at',
            'sheets_synced',
            'sheets_synced_at',
            'sheets_error',
        ]
        read_only_fields = [
            'created_at',
            'updated_at',
            'sheets_synced',
            'sheets_synced_at',
            'sheets_error',
        ]

    def validate_store_id(self, value):
        value = (value or '').strip()

        if not value:
            raise serializers.ValidationError('Informe o Store ID.')

        qs = Pharmacy.objects.filter(store_id=value)

        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise serializers.ValidationError(
                f'Já existe uma farmácia cadastrada com o Store ID {value}.'
            )

        return value

    def validate_type_access(self, value):
        value = (value or '').strip().upper()
        valid = [choice[0] for choice in Pharmacy.ACCESS_CHOICES]

        if value not in valid:
            raise serializers.ValidationError(
                f'Acesso deve ser um de: {", ".join(valid)}.'
            )

        return value

    def validate_pharmacy(self, value):
        value = (value or '').strip()

        if not value:
            raise serializers.ValidationError('Informe o nome da farmácia.')

        return value

    def validate_city(self, value):
        value = (value or '').strip()

        if not value:
            raise serializers.ValidationError('Informe a cidade.')

        return value

    def validate_system(self, value):
        value = (value or '').strip()

        if not value:
            raise serializers.ValidationError('Informe o sistema.')

        return value

    def validate_version(self, value):
        value = (value or '').strip()

        if not value:
            raise serializers.ValidationError('Informe a versão.')

        return value
