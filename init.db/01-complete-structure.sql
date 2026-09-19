--
-- Complete Database Structure from VM Old
-- 100% Compatible with Existing Application
--

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: update_updated_at_column(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_updated_at_column() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: admin_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_users (
    id integer NOT NULL,
    username character varying(50) NOT NULL,
    password_hash character varying(255) NOT NULL,
    full_name character varying(100),
    role character varying(20) DEFAULT 'operator'::character varying,
    is_active boolean DEFAULT true,
    is_mfa_enabled boolean DEFAULT false,
    mfa_secret character varying(32),
    last_login timestamp without time zone,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: admin_users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.admin_users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: admin_users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.admin_users_id_seq OWNED BY public.admin_users.id;


--
-- Name: nas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.nas (
    id integer NOT NULL,
    nasname text NOT NULL,
    shortname text NOT NULL,
    type text DEFAULT 'other'::text NOT NULL,
    ports integer,
    secret text NOT NULL,
    server text,
    community text,
    description text,
    require_ma text DEFAULT 'auto'::text NOT NULL,
    limit_proxy_state text DEFAULT 'auto'::text NOT NULL
);


--
-- Name: nas_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.nas_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: nas_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.nas_id_seq OWNED BY public.nas.id;


--
-- Name: nasreload; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.nasreload (
    nasipaddress inet NOT NULL,
    reloadtime timestamp with time zone NOT NULL
);


--
-- Name: radacct; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.radacct (
    radacctid bigint NOT NULL,
    acctsessionid text NOT NULL,
    acctuniqueid text NOT NULL,
    username text,
    groupname text,
    realm text,
    nasipaddress inet NOT NULL,
    nasportid text,
    nasporttype text,
    acctstarttime timestamp with time zone,
    acctupdatetime timestamp with time zone,
    acctstoptime timestamp with time zone,
    acctinterval bigint,
    acctsessiontime bigint,
    acctauthentic text,
    connectinfo_start text,
    connectinfo_stop text,
    acctinputoctets bigint,
    acctoutputoctets bigint,
    calledstationid text,
    callingstationid text,
    acctterminatecause text,
    servicetype text,
    framedprotocol text,
    framedipaddress inet,
    framedipv6address inet,
    framedipv6prefix inet,
    framedinterfaceid text,
    delegatedipv6prefix inet,
    class text
);


--
-- Name: radacct_radacctid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.radacct_radacctid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: radacct_radacctid_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.radacct_radacctid_seq OWNED BY public.radacct.radacctid;


--
-- Name: radcheck; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.radcheck (
    id integer NOT NULL,
    username text DEFAULT ''::text NOT NULL,
    attribute text DEFAULT ''::text NOT NULL,
    op character varying(2) DEFAULT '=='::character varying NOT NULL,
    value text DEFAULT ''::text NOT NULL
);


--
-- Name: radcheck_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.radcheck_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: radcheck_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.radcheck_id_seq OWNED BY public.radcheck.id;


--
-- Name: radgroupcheck; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.radgroupcheck (
    id integer NOT NULL,
    groupname text DEFAULT ''::text NOT NULL,
    attribute text DEFAULT ''::text NOT NULL,
    op character varying(2) DEFAULT '=='::character varying NOT NULL,
    value text DEFAULT ''::text NOT NULL
);


--
-- Name: radgroupcheck_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.radgroupcheck_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: radgroupcheck_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.radgroupcheck_id_seq OWNED BY public.radgroupcheck.id;


--
-- Name: radgroupreply; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.radgroupreply (
    id integer NOT NULL,
    groupname text DEFAULT ''::text NOT NULL,
    attribute text DEFAULT ''::text NOT NULL,
    op character varying(2) DEFAULT '='::character varying NOT NULL,
    value text DEFAULT ''::text NOT NULL
);


--
-- Name: radgroupreply_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.radgroupreply_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: radgroupreply_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.radgroupreply_id_seq OWNED BY public.radgroupreply.id;


--
-- Name: radpostauth; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.radpostauth (
    id bigint NOT NULL,
    username text NOT NULL,
    pass text,
    reply text,
    calledstationid text,
    callingstationid text,
    authdate timestamp with time zone DEFAULT now() NOT NULL,
    class text
);


--
-- Name: radpostauth_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.radpostauth_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: radpostauth_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.radpostauth_id_seq OWNED BY public.radpostauth.id;


--
-- Name: radreply; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.radreply (
    id integer NOT NULL,
    username text DEFAULT ''::text NOT NULL,
    attribute text DEFAULT ''::text NOT NULL,
    op character varying(2) DEFAULT '='::character varying NOT NULL,
    value text DEFAULT ''::text NOT NULL
);


--
-- Name: radreply_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.radreply_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: radreply_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.radreply_id_seq OWNED BY public.radreply.id;


--
-- Name: radusergroup; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.radusergroup (
    id integer NOT NULL,
    username text DEFAULT ''::text NOT NULL,
    groupname text DEFAULT ''::text NOT NULL,
    priority integer DEFAULT 0 NOT NULL
);


--
-- Name: hotspot_vouchers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hotspot_vouchers (
    id integer NOT NULL,
    voucher_code character varying(100) NOT NULL,
    username character varying(100) NOT NULL,
    password character varying(100) NOT NULL,
    duration_seconds integer,
    package_name character varying(100),
    rate_limit character varying(100),
    first_login timestamp without time zone,
    expires_at timestamp without time zone,
    locked_mac character varying(50),
    created_at timestamp without time zone DEFAULT now(),
    deleted_at timestamp without time zone
);



--
-- Name: radusergroup_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.radusergroup_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: radusergroup_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.radusergroup_id_seq OWNED BY public.radusergroup.id;


--
-- Name: hotspot_vouchers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hotspot_vouchers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hotspot_vouchers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hotspot_vouchers_id_seq OWNED BY public.hotspot_vouchers.id;


--
-- Name: admin_users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_users ALTER COLUMN id SET DEFAULT nextval('public.admin_users_id_seq'::regclass);


--
-- Name: nas id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nas ALTER COLUMN id SET DEFAULT nextval('public.nas_id_seq'::regclass);


--
-- Name: radacct radacctid; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.radacct ALTER COLUMN radacctid SET DEFAULT nextval('public.radacct_radacctid_seq'::regclass);


--
-- Name: radcheck id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.radcheck ALTER COLUMN id SET DEFAULT nextval('public.radcheck_id_seq'::regclass);


--
-- Name: radgroupcheck id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.radgroupcheck ALTER COLUMN id SET DEFAULT nextval('public.radgroupcheck_id_seq'::regclass);


--
-- Name: radgroupreply id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.radgroupreply ALTER COLUMN id SET DEFAULT nextval('public.radgroupreply_id_seq'::regclass);


--
-- Name: radpostauth id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.radpostauth ALTER COLUMN id SET DEFAULT nextval('public.radpostauth_id_seq'::regclass);


--
-- Name: radreply id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.radreply ALTER COLUMN id SET DEFAULT nextval('public.radreply_id_seq'::regclass);


--
-- Name: radusergroup id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.radusergroup ALTER COLUMN id SET DEFAULT nextval('public.radusergroup_id_seq'::regclass);


--
-- Name: hotspot_vouchers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hotspot_vouchers ALTER COLUMN id SET DEFAULT nextval('public.hotspot_vouchers_id_seq'::regclass);


--
-- Name: admin_users admin_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_users
    ADD CONSTRAINT admin_users_pkey PRIMARY KEY (id);


--
-- Name: admin_users admin_users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_users
    ADD CONSTRAINT admin_users_username_key UNIQUE (username);


--
-- Name: nas nas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nas
    ADD CONSTRAINT nas_pkey PRIMARY KEY (id);


--
-- Name: nasreload nasreload_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nasreload
    ADD CONSTRAINT nasreload_pkey PRIMARY KEY (nasipaddress);


--
-- Name: radacct radacct_acctuniqueid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.radacct
    ADD CONSTRAINT radacct_acctuniqueid_key UNIQUE (acctuniqueid);


--
-- Name: radacct radacct_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.radacct
    ADD CONSTRAINT radacct_pkey PRIMARY KEY (radacctid);


--
-- Name: radcheck radcheck_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.radcheck
    ADD CONSTRAINT radcheck_pkey PRIMARY KEY (id);


--
-- Name: radgroupcheck radgroupcheck_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.radgroupcheck
    ADD CONSTRAINT radgroupcheck_pkey PRIMARY KEY (id);


--
-- Name: radgroupreply radgroupreply_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.radgroupreply
    ADD CONSTRAINT radgroupreply_pkey PRIMARY KEY (id);


--
-- Name: radpostauth radpostauth_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.radpostauth
    ADD CONSTRAINT radpostauth_pkey PRIMARY KEY (id);


--
-- Name: radreply radreply_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.radreply
    ADD CONSTRAINT radreply_pkey PRIMARY KEY (id);


--
-- Name: radusergroup radusergroup_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.radusergroup
    ADD CONSTRAINT radusergroup_pkey PRIMARY KEY (id);


--
-- Name: hotspot_vouchers hotspot_vouchers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hotspot_vouchers
    ADD CONSTRAINT hotspot_vouchers_pkey PRIMARY KEY (id);


--
-- Name: nas_nasname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX nas_nasname ON public.nas USING btree (nasname);


--
-- Name: radacct_active_session_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX radacct_active_session_idx ON public.radacct USING btree (acctuniqueid) WHERE (acctstoptime IS NULL);


--
-- Name: radacct_bulk_close; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX radacct_bulk_close ON public.radacct USING btree (nasipaddress, acctstarttime) WHERE (acctstoptime IS NULL);


--
-- Name: radacct_bulk_timeout; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX radacct_bulk_timeout ON public.radacct USING btree (acctstoptime NULLS FIRST, acctupdatetime);


--
-- Name: radacct_start_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX radacct_start_user_idx ON public.radacct USING btree (acctstarttime, username);


--
-- Name: radcheck_username; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX radcheck_username ON public.radcheck USING btree (username, attribute);


--
-- Name: radgroupcheck_groupname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX radgroupcheck_groupname ON public.radgroupcheck USING btree (groupname, attribute);


--
-- Name: radgroupreply_groupname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX radgroupreply_groupname ON public.radgroupreply USING btree (groupname, attribute);


--
-- Name: radreply_username; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX radreply_username ON public.radreply USING btree (username, attribute);


--
-- Name: radusergroup_username; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX radusergroup_username ON public.radusergroup USING btree (username);
CREATE INDEX IF NOT EXISTS radusergroup_lower_username_idx ON public.radusergroup USING btree (lower(username));

CREATE INDEX IF NOT EXISTS radpostauth_authdate_id_idx ON public.radpostauth USING btree (authdate DESC, id DESC);
CREATE INDEX IF NOT EXISTS radpostauth_username_idx ON public.radpostauth USING btree (username);
CREATE INDEX IF NOT EXISTS radpostauth_reply_idx ON public.radpostauth USING btree (reply);

CREATE INDEX IF NOT EXISTS idx_radacct_clean_username ON public.radacct (REGEXP_REPLACE(LOWER(username), '[^a-z0-9]', '', 'g')) WHERE acctstoptime IS NULL;
CREATE INDEX IF NOT EXISTS idx_radusergroup_clean_username ON public.radusergroup (REGEXP_REPLACE(LOWER(username), '[^a-z0-9]', '', 'g'));
CREATE INDEX IF NOT EXISTS idx_hotspot_clean_username ON public.hotspot_vouchers (REGEXP_REPLACE(LOWER(username), '[^a-z0-9]', '', 'g'));
CREATE INDEX IF NOT EXISTS ont_signal_history_user_rec_idx ON public.ont_signal_history (username, recorded_at DESC);


-- Unique constraints for ON CONFLICT queries in application
ALTER TABLE ONLY public.radcheck
    ADD CONSTRAINT radcheck_username_attribute_unique UNIQUE (username, attribute);

ALTER TABLE ONLY public.radusergroup
    ADD CONSTRAINT radusergroup_username_groupname_unique UNIQUE (username, groupname);


-- =============================================================================
-- DEFAULT DATA
-- =============================================================================

-- Default Admin User
INSERT INTO public.admin_users (username, password_hash, full_name, role, is_active) 
VALUES ('admin', 'scrypt:32768:8:1$vFRJwsglT5kUjd3Y$f8990331324d261ba54588e58e257f791ce506856617fbb523fe05860a256720284e6a019a8991c588bc9821f165ad40964a9df43951ac75e4b2c2c160a0b9dc', 'Administrator', 'admin', true);

-- Set sequence values
SELECT setval('public.admin_users_id_seq', 5, true);
SELECT setval('public.nas_id_seq', 4, true);

-- =============================================================================
-- AUTOMATIC DUPLICATE SESSION CLEANUP TRIGGER
-- =============================================================================
CREATE INDEX IF NOT EXISTS radacct_active_username_idx 
ON public.radacct (LOWER(username)) 
WHERE acctstoptime IS NULL;

CREATE OR REPLACE FUNCTION public.radacct_close_duplicate_sessions()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.acctstoptime IS NULL AND NEW.username IS NOT NULL AND NEW.username <> '' THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.radusergroup 
            WHERE LOWER(username) = LOWER(NEW.username) 
              AND groupname IN ('mikrotik-full', 'mikrotik-write', 'mikrotik-read')
        ) THEN
            UPDATE public.radacct
            SET acctstoptime = COALESCE(acctupdatetime, acctstarttime, NOW()),
                acctterminatecause = 'Lost-Carrier'
            WHERE LOWER(username) = LOWER(NEW.username)
              AND acctstoptime IS NULL
              AND radacctid != COALESCE(NEW.radacctid, 0)
              AND acctuniqueid != NEW.acctuniqueid;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_radacct_close_duplicates ON public.radacct;
CREATE TRIGGER trg_radacct_close_duplicates
AFTER INSERT ON public.radacct
FOR EACH ROW
EXECUTE FUNCTION public.radacct_close_duplicate_sessions();
