do $t$
begin
 
perform pkg_util_interface.p_upd_external_account_type(piv_external_account_type_code => 'OIDCAuth'
                                                     ,piv_external_account_type_desc  => 'Open Id Connect Auth'
                                                     ,piv_java_interface              => 'com.vitechinc.gcore.external.system.services.IV3ExternalAdapter'
                                                     ,piv_product_flag                => 'Y');
 
perform pkg_util_interface.p_upd_external_accounts(piv_external_account_name     => 'OIDCAuth for MSS'
                                                 ,piv_external_account_code      => 'OIDCAuth'
                                                 ,piv_ip_address                 => ''
                                                 ,piv_username                   => ''
                                                 ,piv_password                   => '${LFGTESTENABLESSO}'
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
      ,piv_display_name => 'OIDC based authentication'
      ,piv_internal_name => 'OIDC_AUTH'
      ,pib_product_flag => 'Y');
 
perform pkg_util_security.p_update_sec_provider_detail
      (piv_property_name => 'EXTERNAL_ACCOUNT_CODE'
      ,piv_property_value => 'OIDCAuth'
      ,piv_internal_name => 'OIDC_AUTH');
 
perform pkg_util_security.p_update_sec_provider_detail
      (piv_property_name => 'TOKEN_URL'
      ,piv_property_value => 'https://v3locity.auth0.com/oauth/token'
      ,piv_internal_name => 'OIDC_AUTH');
 
perform pkg_util_security.p_update_sec_provider_detail
      (piv_property_name => 'USERINFO_URL'
      ,piv_property_value => 'https://v3locity.auth0.com/userinfo'
      ,piv_internal_name => 'OIDC_AUTH');
 
perform pkg_util_security.p_update_sec_provider_detail
      (piv_property_name => 'CLIENT_ID'
      ,piv_property_value => '6xOv3PpPQpktKpteinyTyMdW0MNnkkV7'
      ,piv_internal_name => 'OIDC_AUTH');
 
perform pkg_util_security.p_update_sec_provider_detail
      (piv_property_name => 'REDIRECT_URL'
      ,piv_property_value => 'https://lfgtest.v3locitydev.com/app'
      ,piv_internal_name => 'OIDC_AUTH');
 
perform pkg_util_security.p_update_sec_provider_detail
      (piv_property_name => 'IDP_URL'
      ,piv_property_value => 'https://launcher.myapps.microsoft.com/api/signin/0ae445b7-8f8c-4ce3-a4a5-853c2884ffee?tenantId=873a355e-15b1-43df-8da4-f98426fb7241'
      ,piv_internal_name => 'OIDC_AUTH');
end;
$t$;
 
do $t$
begin
UPDATE dbo.security_application
   SET security_provider_id =
       (SELECT security_provider_id
          FROM dbo.security_provider
         WHERE internal_name = 'OIDC_AUTH')
 WHERE internal_name IN ('USER');
end; $t$;