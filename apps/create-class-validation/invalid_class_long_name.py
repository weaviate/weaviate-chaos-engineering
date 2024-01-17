import weaviate

client = weaviate.Client("http://localhost:8080")

def invalid_long_classname(client: weaviate.Client):
    client.schema.delete_all()
    class_obj = {
        "vectorizer": "none",
        "vectorIndexConfig": {
            "efConstruction": 64,
            "maxConnections": 4,
            "cleanupIntervalSeconds": 10,
        },
        "class": "group_test_sitegroundsiteground_offers_web_hosting_services_crafted_for_top_speed_unmatched_security_and_247_expert_support_catering_to_over_2800000_domains_worldwide_their_products_include_web_hosting_for_various_platforms_like_wordpress_joomla_magento_drupal_prestashop_and_woocommerce_with_a_focus_on_security_speed_and_customer_support_encoding_labse_mb77_01",
        "invertedIndexConfig": {
            "indexTimestamps": False,
        },
        "properties": [
            {
                "dataType": ["boolean"],
                "name": "bool_field",
            },
            {
                "dataType": ["int"],
                "name": "field_1",
            },
            {
                "dataType": ["int"],
                "name": "field_2",
            },
            {
                "dataType": ["int"],
                "name": "field_3",
            },
        ],
    }
    client.schema.create_class(class_obj)
    response = client.schema.get();
    print(response);
    assert (response.status_code == 200)
    return response;

invalid_long_classname(client)



