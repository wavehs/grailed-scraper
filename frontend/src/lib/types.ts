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

export type DashboardProductType = 'footwear' | 'clothing' | 'accessories';

export type DashboardRow = {
  id: number;
  name: string;
  brand_name: string;
  category?: string;
  available_sizes: string[];
  available_conditions: string[];
  sold_count: number;
  exact_sold_count: number;
  active_count: number;
  median_sold_price: number | null;
  median_days_to_sell: string | null;
  median_sold_likes: string | null;
  liquidity_score: string | null;
  demand_score: string | null;
  price_score: string;
  confidence_score: string;
  market_opportunity_score: string | null;
  scoring_status: 'scored' | 'insufficient_sales' | 'insufficient_temporal_data';
  model_version: string;
  window_days: number;
  run_id: number;
};

export type CursorPage<T> = {
  data: T[];
  limit: number;
  next_cursor: string | null;
};

export type BrandAnalyticsRow = {
  id: number;
  name: string;
  groups_count: number;
  sold_count: number;
  exact_sold_count: number;
  active_count: number;
  median_sold_price: number | null;
  median_days_to_sell: string | null;
  median_sold_likes: string | null;
  sell_through?: string | null;
  demand_score: string | null;
  liquidity_score: string | null;
  confidence_score: string;
  market_opportunity_score: string | null;
  scoring_status: 'scored' | 'insufficient_sales' | 'insufficient_temporal_data';
  average_liquidity_score?: string | null;
  average_demand_score?: string | null;
  average_confidence_score?: string;
  average_market_opportunity_score?: string | null;
};

export type BrandAnalyticsList = CursorPage<BrandAnalyticsRow>;

export type ScoreComponent = {
  score: string;
  weight?: string;
  liquidity_weight?: string;
  demand_weight?: string;
};
export type ListingExample = {
  id: number;
  grailed_id: number;
  title: string;
  price: number;
  likes: number;
  sold_at?: string;
  created_at?: string;
  days_on_market?: number | null;
};
export type VariantPerformance = {
  value: string | null;
  sold_count: number;
  active_count: number;
  sell_through: string;
};
export type ModelGroupDetail = {
  id: number;
  name: string;
  brand: string;
  category?: string;
  group_type: string;
  model_version: string;
  window_days: number;
  run_id: number;
  input_digest: string;
  variant_breakdown: {
    colors: VariantPerformance[];
    sizes: VariantPerformance[];
  };
  metrics: {
    sold_count: number;
    exact_sold_count: number;
    active_count: number;
    sell_through: string;
    median_sold_price?: number;
    median_days_to_sell?: string;
    median_sold_likes?: string;
    liquidity_score: string | null;
    demand_score: string | null;
    price_score: string;
    confidence_score: string;
    market_opportunity_score: string | null;
    scoring_status: 'scored' | 'insufficient_sales' | 'insufficient_temporal_data';
    components: Record<string, ScoreComponent>;
    confidence_factors: Record<string, unknown>;
    quality_summary: Record<string, unknown>;
    warnings: string[];
  };
  sold_examples: ListingExample[];
  active_examples: ListingExample[];
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
  title: string;
  brand: string;
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
