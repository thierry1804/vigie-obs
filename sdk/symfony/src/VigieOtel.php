<?php

declare(strict_types=1);

namespace Etech\VigieOtel;

use OpenTelemetry\Contrib\Otlp\OtlpHttpTransportFactory;
use OpenTelemetry\SDK\Trace\TracerProvider;
use OpenTelemetry\SDK\Trace\SpanProcessor\SimpleSpanProcessor;
use OpenTelemetry\SDK\Resource\ResourceInfo;
use OpenTelemetry\SDK\Common\Attribute\Attributes;

final class VigieOtel
{
    public static function init(
        string $serviceName,
        string $tenantId = 'default',
        ?string $otlpEndpoint = null,
    ): void {
        $endpoint = $otlpEndpoint ?? getenv('VIGIE_OTEL_ENDPOINT') ?: 'http://localhost:4318/v1/traces';
        $transport = (new OtlpHttpTransportFactory())->create($endpoint, 'application/x-protobuf');
        $resource = ResourceInfo::create(Attributes::create([
            'service.name' => $serviceName,
            'tenant.id' => $tenantId,
        ]));
        $provider = TracerProvider::builder()
            ->addSpanProcessor(new SimpleSpanProcessor(/* exporter from transport */))
            ->setResource($resource)
            ->build();
        // Auto-instrumentation HTTP/SQL via framework hooks — configuration minimale V2
    }
}
