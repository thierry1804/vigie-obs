const { NodeSDK } = require("@opentelemetry/sdk-node");
const { getNodeAutoInstrumentations } = require("@opentelemetry/auto-instrumentations-node");
const { OTLPTraceExporter } = require("@opentelemetry/exporter-trace-otlp-http");
const { Resource } = require("@opentelemetry/resources");
const { ATTR_SERVICE_NAME } = require("@opentelemetry/semantic-conventions");

/**
 * Initialise le tracing VIGIE (optionnel — upsell).
 * @param {object} opts
 * @param {string} opts.serviceName
 * @param {string} [opts.otlpEndpoint] default http://localhost:4318/v1/traces
 * @param {string} [opts.tenantId]
 */
function initVigieOtel(opts) {
  const endpoint = opts.otlpEndpoint || process.env.VIGIE_OTEL_ENDPOINT || "http://localhost:4318/v1/traces";
  const sdk = new NodeSDK({
    resource: new Resource({
      [ATTR_SERVICE_NAME]: opts.serviceName,
      "tenant.id": opts.tenantId || "default",
    }),
    traceExporter: new OTLPTraceExporter({ url: endpoint }),
    instrumentations: [getNodeAutoInstrumentations()],
  });
  sdk.start();
  return sdk;
}

module.exports = { initVigieOtel };
