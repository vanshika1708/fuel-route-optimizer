from rest_framework import serializers


class StrictStringField(serializers.CharField):
    def to_internal_value(self, data: object) -> str:
        if not isinstance(data, str):
            raise serializers.ValidationError("A valid string is required.")
        return super().to_internal_value(data)


class RouteRequestSerializer(serializers.Serializer):
    start = StrictStringField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        max_length=200,
        error_messages={"required": "Start location is required.", "blank": "Start location is required."},
    )
    finish = StrictStringField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        max_length=200,
        error_messages={"required": "Finish location is required.", "blank": "Finish location is required."},
    )