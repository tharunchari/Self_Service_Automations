do $t$
begin
 
perform pkg_util_interface.p_upd_external_account_type(piv_external_account_type_code => 'OIDCAuth'
                                                     ,piv_external_account_type_desc  => 'Open Id Connect Auth'
                                                     ,piv_java_interface              => 'com.vitechinc.gcore.external.system.services.IV3ExternalAdapter'
                                                     ,piv_product_flag                => 'Y');
 
perform pkg_util_interface.p_upd_external_accounts(piv_external_account_name     => 'OIDCAuth for CoreAdmin'
                                                 ,piv_external_account_code      => 'OIDCAuthCA'
                                                 ,piv_ip_address                 => ''
                                                 ,piv_username                   => ''
                                                 ,piv_password                   => '${LFGPreProd_CA_Enable_SSO}'
                                                 ,piv_external_account_type      => ''
                                                 ,piv_end_point                  => ''
                                                 ,pib_active_flag                => 'Y'
                                                 ,piv_external_account_type_code => 'OIDCAuth'
                                                 ,piv_product_flag               => 'Y');
 
perform pkg_util_system.p_update_attribute_value
      (pv_attribute_name => 'AUTH_TYPE'
      ,pv_attribute_type => 'CD'
      ,pv_internal_value => 'OI'
      ,pv_description => 'OIDC Provider'
      ,pn_seq_no => 4
      ,pv_user_value => 'OI');
 
 perform pkg_util_security.p_update_security_provider
      (piv_auth_type => 'OI'
      ,piv_description => 'OIDC based Single Sign on'
      ,piv_display_name => 'OIDC based authentication for CoreAdmin'
      ,piv_internal_name => 'OIDC_AUTH_CA'
      ,pib_product_flag => 'Y');
 
perform pkg_util_security.p_update_sec_provider_detail
      (piv_property_name => 'EXTERNAL_ACCOUNT_CODE'
      ,piv_property_value => 'OIDCAuthCA'
      ,piv_internal_name => 'OIDC_AUTH_CA');
 
perform pkg_util_security.p_update_sec_provider_detail
      (piv_property_name => 'TOKEN_URL'
      ,piv_property_value => 'https://v3locity.auth0.com/oauth/token'
      ,piv_internal_name => 'OIDC_AUTH_CA');
 
perform pkg_util_security.p_update_sec_provider_detail
      (piv_property_name => 'USERINFO_URL'
      ,piv_property_value => 'https://v3locity.auth0.com/userinfo'
      ,piv_internal_name => 'OIDC_AUTH_CA');
 
perform pkg_util_security.p_update_sec_provider_detail
      (piv_property_name => 'CLIENT_ID'
      ,piv_property_value => 'Cp0A7DNWwkYEDp7Z4CCIvSJhbu0nQu2t'
      ,piv_internal_name => 'OIDC_AUTH_CA');
 
perform pkg_util_security.p_update_sec_provider_detail
      (piv_property_name => 'REDIRECT_URL'
      ,piv_property_value => 'https://41wqscmn2c.execute-api.us-east-1.amazonaws.com/prod'
      ,piv_internal_name => 'OIDC_AUTH_CA');
 
perform pkg_util_security.p_update_sec_provider_detail
      (piv_property_name => 'IDP_URL'
      ,piv_property_value => 'https://v3locity.auth0.com/authorize?redirect_uri=https://41wqscmn2c.execute-api.us-east-1.amazonaws.com/prod&response_type=code&client_id=Cp0A7DNWwkYEDp7Z4CCIvSJhbu0nQu2t&scope=openid%20profile%20email&state=https://lfgpreprod.v3locity.com/app'
      ,piv_internal_name => 'OIDC_AUTH_CA');

end;
$t$;
 
do $t$
begin
UPDATE dbo.security_application
   SET security_provider_id =
       (SELECT security_provider_id
          FROM dbo.security_provider
         WHERE internal_name = 'OIDC_AUTH_CA')
 WHERE internal_name IN ('USER');
end; $t$;
