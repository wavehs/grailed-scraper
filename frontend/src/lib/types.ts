export type SourceMode = 'live';
export type RunStatus =
  'pending' | 'running' | 'completed' | 'partial' | 'failed' | 'interrupted' | 'cancelled';

export type ApiHealth = {
  status: 'ok';
  service: string;
  source_mode: SourceMode;
  request_id: string;
};

export type ParserHealth = {
  status: 'ready' | 'degraded' | 'unavailable';
  source_mode: SourceMode;
  transports: Record<string, boolean>;
  discovery: { available: boolean; status: string; discovered_at?: string; valid_until?: string };
  schema: {
    detected_at?: string;
    drift_score?: string;
    active_alerts: number;
    alerts: Array<{
      id: number;
      severity: string;
      message: string;
      details: Record<string, unknown>;
      created_at: string;
    }>;
  };
  active_runs: number[];
  reasons: string[];
  versions: { curl_cffi?: string };
  circuits: Array<{ tier: string; host: string; state: string }>;
  compliance: {
    live_acknowledged: boolean;
    seller_identity_mode: 'none' | 'hashed' | 'plain';
    limits: { requests_per_minute: number; max_concurrency: number };
  };
  last_run?: {
    id: number;
    status: string;
    degraded: boolean;
    tier?: string;
    metrics: RunMetrics;
  };
};

export type RunSummary = {
  id: number;
  mode: string;
  status: RunStatus;
  phase: string;
  degraded: boolean;
  tier?: string;
  budget?: { brands?: number; tasks?: number; full_brands?: string[] };
  coverage?: string;
  requests_made: number;
  warnings: string[];
  created_at: string;
  started_at?: string;
  finished_at?: string;
  heartbeat_at?: string;
};

export type RunList = { data: RunSummary[]; total: number; limit: number; offset: number };
export type RunTask = {
  id: number;
  brand_id?: number;
  index_type: string;
  status: string;
  attempts: number;
  hits_collected: number;
  expected_hits?: number;
  coverage?: string;
  tier?: string;
  error?: string;
};
export type RunProgress = {
  status: RunStatus;
  phase: string;
  tier?: string;
  degraded: boolean;
  brands_total: number;
  brands_completed: number;
  tasks_total: number;
  tasks_done: number;
  hits_fetched: number;
  requests_made: number;
  coverage?: string;
  partial: boolean;
  truncated: boolean;
  current_brand?: string;
  tasks_failed: number;
  eta_seconds?: number;
  heartbeat_at?: string;
  warnings: string[];
  errors: Array<{
    task_id: number;
    brand_id?: number;
    index_type: string;
    code: string;
  }>;
};
export type RunReport = {
  run: RunSummary;
  stats: Record<string, unknown>;
  metrics: RunMetrics;
  coverage_by_brand: Record<string, string | null>;
  tasks: RunTask[];
};
export type RunMetrics = {
  requests_total: number;
  requests_by_tier: Record<string, number>;
  http_errors_by_code: Record<string, number>;
  retries: number;
  rate_limit_hits: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  cache_hit_rate: number;
  hits_fetched: number;
  listings_inserted: number;
  listings_updated: number;
  listings_invalid: number;
  duration_s: number;
};
export type RunStartResponse = { run: RunSummary };
export type RunDetail = { run: RunSummary; tasks: RunTask[] };

export type DesignerSuggestion = { name: string; listings_count: number };

export type Mapping = {
  id: number;
  source_designer_name: string;
  source_slug?: string;
  listings_count: number;
  match_score: string;
  match_method: string;
  is_subbrand: boolean;
  state: 'verified' | 'review' | 'rejected';
};
export type Brand = {
  id: number;
  name: string;
  aliases: string[];
  include_subbrands: boolean;
  listings_count: number;
  status: 'verified' | 'review' | 'unresolved';
  mappings: Mapping[];
};
export type BrandList = { data: Brand[] };

export type CursorPage<T> = {
  data: T[];
  limit: number;
  next_cursor: string | null;
};

export type SettingOrigin = 'default' | 'env' | 'database';
export type SettingValue = string | number | boolean | null;
export type SettingEntry = { value: SettingValue; origin: SettingOrigin };
export type SettingsResponse = { groups: Record<string, Record<string, SettingEntry>> };
export type DiscoveryResponse = {
  source: 'grailed';
  status: string;
  method?: string;
  discovered_at?: string;
  expires_at?: string;
  active_index?: string;
  sold_index?: string;
  brand_facet?: string;
  can_browse: boolean;
  pagination_limit?: number;
  max_hits_per_page?: number;
  schema_sample_size: number;
  schema_field_count: number;
  drift_score: number;
  alerts: Array<{ severity: string; kind: string; path: string }>;
};

export type CatalogListing = {
  id: number;
  grailed_id: number;
  url: string;
  title: string;
  brand: string;
  brand_id: number | null;
  product_type: string | null;
  status: string;
  size?: string;
  color?: string;
  price: number;
  created_at?: string;
  sold_at?: string;
  last_seen_at: string;
  days_on_market?: number | null;
  model_group_id?: number;
  model_name?: string;
  is_fallback: boolean;
  model_sold_count: number;
  model_active_count: number;
};
export type CatalogListingList = CursorPage<CatalogListing>;

export type TaxonomySection = { id: string; ru: string; en: string };
export type TaxonomyType = { id: string; section: string; ru: string; en: string };
export type Taxonomy = { version: string; sections: TaxonomySection[]; types: TaxonomyType[] };

export type GroupStatus = 'confirmed' | 'auto' | 'ignored';

export type GroupSummary = {
  id: number;
  brand_id: number;
  brand: string;
  product_type: string;
  slug: string;
  name: string;
  aliases: string[];
  parent_id: number | null;
  status: GroupStatus;
  source: string;
  is_fallback: boolean;
  listings: number;
  sold: number;
  active: number;
};

export type GroupDetail = GroupSummary & {
  parent: GroupSummary | null;
  versions: GroupSummary[];
};

export type GroupList = { data: GroupSummary[]; total: number };

export type TrendLevel = 'model' | 'type' | 'brand';
export type TrendSort = 'trend' | 'growth' | 'speed' | 'sales' | 'price' | 'supply' | 'new';

export type TrendRow = {
  scope: TrendLevel;
  scope_key: string;
  group_id: number | null;
  brand_id: number;
  brand: string;
  product_type: string | null;
  section: string | null;
  name: string | null;
  status: GroupStatus | null;
  is_fallback: boolean;
  versions: number;
  listings: number;
  sold: number;
  sold_7d: number;
  sold_30d: number;
  sold_prev_30d: number;
  sold_90d: number;
  growth: string;
  speed: string | null;
  trend_score: string | null;
  median_days_to_sell: string | null;
  sell_through_30d: string;
  median_price: number | null;
  price_change: string | null;
  active_now: number;
  new_listings_14d: number;
  is_new: boolean;
  first_seen_at: string | null;
  weekly_sales: number[];
};

export type TrendList = { data: TrendRow[]; total: number; computed_at: string | null };

export type TrendVariant = { value: string; sold: number; active: number; sell_through: string };

export type TrendListing = {
  id: number;
  grailed_id: number;
  url: string;
  title: string;
  price: number;
  status: string;
  sold_at: string | null;
  created_at: string | null;
  days_to_sell: number | null;
  size: string | null;
  color: string | null;
  group_id: number | null;
  group_name: string | null;
  relisted: boolean;
};

export type TrendCard = {
  group: GroupDetail;
  metrics: TrendRow | null;
  computed_at: string | null;
  weekly_median_price: Array<number | null>;
  colors: TrendVariant[];
  sizes: TrendVariant[];
  versions: TrendRow[];
  type_metrics: TrendRow | null;
  recent_sales: TrendListing[];
  active_listings: TrendListing[];
};

export type TrendFilters = {
  level: TrendLevel;
  brandIds: number[];
  section: string;
  productType: string;
  window: 7 | 30 | 90;
  priceMin: string;
  priceMax: string;
  newOnly: boolean;
  minSales: number;
  search: string;
  sort: TrendSort;
  desc: boolean;
};
