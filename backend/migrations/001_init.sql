--
-- PostgreSQL database dump
--

\restrict Ee0qZq6RrXJV1o9VVk3cYUirfyslZdQ9L69aW551fh3OHsO4z3TjLlMohMO7QS6

-- Dumped from database version 14.22 (Ubuntu 14.22-0ubuntu0.22.04.1)
-- Dumped by pg_dump version 14.22 (Ubuntu 14.22-0ubuntu0.22.04.1)

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

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alerts (
    id bigint NOT NULL,
    source character varying(16) NOT NULL,
    event_id bigint,
    device_id bigint NOT NULL,
    patient_id bigint,
    alert_type character varying(64) NOT NULL,
    alert_level character varying(16) NOT NULL,
    message text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    payload_json jsonb,
    is_read boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: alerts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.alerts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: alerts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.alerts_id_seq OWNED BY public.alerts.id;


--
-- Name: clips; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.clips (
    id bigint NOT NULL,
    event_id bigint,
    s3_key text NOT NULL,
    file_name character varying(255),
    started_at timestamp with time zone,
    ended_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: clips_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.clips_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: clips_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.clips_id_seq OWNED BY public.clips.id;


--
-- Name: devices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.devices (
    id bigint NOT NULL,
    device_key character varying(64) NOT NULL,
    device_name character varying(100),
    location character varying(100),
    status character varying(32) DEFAULT 'active'::character varying,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: devices_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.devices_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: devices_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.devices_id_seq OWNED BY public.devices.id;


--
-- Name: events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.events (
    id bigint NOT NULL,
    device_id bigint NOT NULL,
    patient_id bigint,
    event_type character varying(64) NOT NULL,
    confidence double precision,
    severity integer DEFAULT 0 NOT NULL,
    event_status character varying(32) DEFAULT 'detected'::character varying,
    occurred_at timestamp with time zone NOT NULL,
    clip_url text,
    payload_json jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.events_id_seq OWNED BY public.events.id;


--
-- Name: patient_guardians; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.patient_guardians (
    user_id bigint NOT NULL,
    patient_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: patients; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.patients (
    id bigint NOT NULL,
    patient_code character varying(64) NOT NULL,
    name character varying(100),
    gender character varying(16),
    birth_date date,
    status character varying(32) DEFAULT 'active'::character varying,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: patients_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.patients_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: patients_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.patients_id_seq OWNED BY public.patients.id;


--
-- Name: risk_scores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.risk_scores (
    id bigint NOT NULL,
    patient_id bigint NOT NULL,
    score integer NOT NULL,
    risk_level character varying(16),
    reason text,
    analyzed_from timestamp with time zone,
    analyzed_to timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: risk_scores_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.risk_scores_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: risk_scores_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.risk_scores_id_seq OWNED BY public.risk_scores.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id bigint NOT NULL,
    email character varying(255) NOT NULL,
    password_hash character varying(255) NOT NULL,
    role character varying(16) DEFAULT 'guardian'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: alerts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts ALTER COLUMN id SET DEFAULT nextval('public.alerts_id_seq'::regclass);


--
-- Name: clips id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clips ALTER COLUMN id SET DEFAULT nextval('public.clips_id_seq'::regclass);


--
-- Name: devices id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devices ALTER COLUMN id SET DEFAULT nextval('public.devices_id_seq'::regclass);


--
-- Name: events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events ALTER COLUMN id SET DEFAULT nextval('public.events_id_seq'::regclass);


--
-- Name: patients id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patients ALTER COLUMN id SET DEFAULT nextval('public.patients_id_seq'::regclass);


--
-- Name: risk_scores id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_scores ALTER COLUMN id SET DEFAULT nextval('public.risk_scores_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: alerts alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_pkey PRIMARY KEY (id);


--
-- Name: clips clips_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clips
    ADD CONSTRAINT clips_pkey PRIMARY KEY (id);


--
-- Name: devices devices_device_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devices
    ADD CONSTRAINT devices_device_key_key UNIQUE (device_key);


--
-- Name: devices devices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.devices
    ADD CONSTRAINT devices_pkey PRIMARY KEY (id);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: patient_guardians patient_caregivers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patient_guardians
    ADD CONSTRAINT patient_caregivers_pkey PRIMARY KEY (user_id, patient_id);


--
-- Name: patients patients_patient_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patients
    ADD CONSTRAINT patients_patient_code_key UNIQUE (patient_code);


--
-- Name: patients patients_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patients
    ADD CONSTRAINT patients_pkey PRIMARY KEY (id);


--
-- Name: risk_scores risk_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_scores
    ADD CONSTRAINT risk_scores_pkey PRIMARY KEY (id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: alerts alerts_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.devices(id) ON DELETE CASCADE;


--
-- Name: alerts alerts_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id) ON DELETE SET NULL;


--
-- Name: alerts alerts_patient_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_patient_id_fkey FOREIGN KEY (patient_id) REFERENCES public.patients(id) ON DELETE SET NULL;


--
-- Name: clips clips_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clips
    ADD CONSTRAINT clips_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id) ON DELETE CASCADE;


--
-- Name: events events_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.devices(id) ON DELETE CASCADE;


--
-- Name: events events_patient_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_patient_id_fkey FOREIGN KEY (patient_id) REFERENCES public.patients(id) ON DELETE SET NULL;


--
-- Name: patient_guardians patient_caregivers_patient_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patient_guardians
    ADD CONSTRAINT patient_caregivers_patient_id_fkey FOREIGN KEY (patient_id) REFERENCES public.patients(id) ON DELETE CASCADE;


--
-- Name: patient_guardians patient_caregivers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patient_guardians
    ADD CONSTRAINT patient_caregivers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: risk_scores risk_scores_patient_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_scores
    ADD CONSTRAINT risk_scores_patient_id_fkey FOREIGN KEY (patient_id) REFERENCES public.patients(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict Ee0qZq6RrXJV1o9VVk3cYUirfyslZdQ9L69aW551fh3OHsO4z3TjLlMohMO7QS6

