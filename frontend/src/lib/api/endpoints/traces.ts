/**
 * tracesAPI — BFF Trace API client (D13 §5 C). Browser talks to BFF only.
 */

import { apiClient } from '../client';

export type TraceSpan = {
  spanID?: string;
  span_id?: string;
  operationName?: string;
  operation_name?: string;
  name?: string;
  duration?: number;
  duration_us?: number;
  parentSpanId?: string;
  parent_span_id?: string;
  references?: Array<{
    refType?: string;
    ref_type?: string;
    spanID?: string;
    span_id?: string;
  }>;
  tags?: Array<{ key?: string; value?: unknown }>;
  [key: string]: unknown;
};

export type TraceSpansResponse = {
  trace_id: string;
  spans: TraceSpan[];
  tenant_id?: string;
};

export const tracesAPI = {
  getSpans: async (traceId: string): Promise<TraceSpansResponse> => {
    const response = await apiClient.get<TraceSpansResponse>(
      `/traces/${encodeURIComponent(traceId)}/spans`
    );
    const spans = Array.isArray(response?.spans) ? response.spans : [];
    return {
      trace_id: response?.trace_id ?? traceId,
      spans,
      tenant_id: response?.tenant_id,
    };
  },
};
