<?php
/**
 * get-all-urls.php
 * Collects ALL public WordPress URLs and saves to wp-content/uploads/all-urls.json
 * Visit once in browser, then Python reads the file directly from disk.
 * Access: http://tesstaiwan-local.local/get-all-urls.php
 */
set_time_limit(300);
require_once __DIR__ . '/wp-load.php';

$urls = [];

// 1. All published posts (all post types: post, page, custom)
$post_types = get_post_types(['public' => true], 'names');
foreach ($post_types as $post_type) {
    $posts = get_posts([
        'post_type'      => $post_type,
        'post_status'    => 'publish',
        'posts_per_page' => -1,
        'fields'         => 'ids',
    ]);
    foreach ($posts as $id) {
        $link = get_permalink($id);
        if ($link) $urls[] = $link;
    }
}

// 2. Homepage
$urls[] = home_url('/');

// 每頁文章數（後面各段都會用到）
$posts_per_page = (int) get_option('posts_per_page') ?: 10;

// 3. All category / tag / taxonomy archive pages（含分頁）
$taxonomies = get_taxonomies(['public' => true], 'names');
foreach ($taxonomies as $taxonomy) {
    $terms = get_terms(['taxonomy' => $taxonomy, 'hide_empty' => true, 'number' => 0]);
    if (is_wp_error($terms)) continue;
    foreach ($terms as $term) {
        $link = get_term_link($term);
        if (is_wp_error($link)) continue;
        $urls[] = $link; // 第 1 頁
        // 加入第 2 頁起的分頁 URL
        $term_pages = (int) ceil($term->count / $posts_per_page);
        for ($i = 2; $i <= $term_pages; $i++) {
            $urls[] = trailingslashit($link) . 'page/' . $i . '/';
        }
    }
}

// 4. Author archive pages
$authors = get_users(['who' => 'authors', 'fields' => 'ID']);
foreach ($authors as $id) {
    $urls[] = get_author_posts_url($id);
}

// 5. Date archives (year/month)
global $wpdb;
$dates = $wpdb->get_results(
    "SELECT DISTINCT YEAR(post_date) as y, MONTH(post_date) as m
     FROM {$wpdb->posts}
     WHERE post_status = 'publish' AND post_type = 'post'
     ORDER BY y DESC, m DESC"
);
foreach ($dates as $d) {
    $urls[] = get_month_link($d->y, $d->m);
    $urls[] = get_year_link($d->y);
}

// 6. Pagination for main blog index
$total_posts = (int) wp_count_posts()->publish;
$total_pages = (int) ceil($total_posts / $posts_per_page);
for ($i = 2; $i <= $total_pages; $i++) {
    $urls[] = home_url('/page/' . $i . '/');
}

// Deduplicate
$urls = array_values(array_unique(array_filter($urls)));
$data = ['count' => count($urls), 'urls' => $urls];

// Save to disk (Python reads this file directly)
$out_path = __DIR__ . '/wp-content/uploads/all-urls.json';
file_put_contents($out_path, json_encode($data, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT));

header('Content-Type: application/json; charset=utf-8');
echo json_encode(['status' => 'done', 'count' => count($urls), 'saved_to' => $out_path]);
